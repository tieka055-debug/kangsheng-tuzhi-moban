"""Self-contained fictional PDFs and fake brand assets; no customer data needed."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import pymupdf as fitz

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import kangsheng as k


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.base=Path(cls.temp.name)
        source=fitz.open();p=source.new_page(width=k.PAGE[0],height=k.PAGE[1])
        p.insert_text((30,35),'SYNTHETIC MODEL-X R125',fontsize=12)
        p.draw_rect(fitz.Rect(50,105,130,150),color=(0,0,0))
        p.insert_text((45,95),'1.80 +/-0.05',fontsize=9)
        p.draw_line((45,170),(140,170),color=(0,0,0))
        p.insert_text((48,166),'DIM A 4.30',fontsize=8)
        p.draw_rect(fitz.Rect(430,65,575,115),color=(0,0,0))
        p.insert_text((435,79),'PART NO.    DIM A',fontsize=8)
        p.insert_text((435,94),'X-3P-R125    4.80',fontsize=8)
        p.insert_text((435,109),'X-4P-R125    6.40',fontsize=8)
        p.insert_text((435,241),'CURRENT: 2.0 A',fontsize=9)
        p.insert_text((435,258),'VOLTAGE: 12 V',fontsize=9)
        p.insert_text((435,275),'TEMP: -40 to +85 C',fontsize=9)
        cls.source=cls.base/'source.pdf';source.save(cls.source)
        font=cls.base/'title.font';font.write_bytes(fitz.Font(fontname='helv').buffer)
        for name,w,h in [('background',842,596),('brand-strip',900,140)]:
            d=fitz.open();page=d.new_page(width=w,height=h)
            if name=='brand-strip':page.insert_text((70,70),'SYNTHETIC BRAND',fontsize=34,color=k.BLUE)
            page.get_pixmap(alpha=False).save(cls.base/(name+'.png'))
        cls.template={'schema_version':1,'source':{'path':str(cls.source),'sha256':k.digest(cls.source),
             'page':1,'rotation':0,'expected_pages':1},'renderer':'auto',
          'identity':{'expected_model':'MODEL-XR125','observed_model':'MODEL-X R125',
             'model_evidence':'Synthetic source header','observed_parts':['X-3P-R125','X-4P-R125'],
             'part_pattern':r'X-[34]P-R125'},
          'fields':{'model':'MODEL-XR125','title':'CONNECTOR','scale_text':'','unit':'mm','sheet':'1/1','tolerances':[]},
          'assets':{'background':str(cls.base/'background.png'),'brand_strip':str(cls.base/'brand-strip.png'),'font':str(font)},
          'groups':[{'id':'view','kind':'view','clips':[[40,80,200,160],[40,160,150,180]],'dst':[60,100],'scale':1},
             {'id':'table','kind':'table','clips':[[420,60,580,120]],'dst':[574,50],'scale':1},
             {'id':'specs','kind':'performance','clips':[[420,220,590,285]],'dst':[570,250],'scale':1}],
          'coverage':{'include':[[10,10,832,586]],'exclude':[{'box':[20,10,400,60],
             'reason':'Source synthetic title copied via reviewed title field'}]}}

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def job(self,name,edit=None):
        cfg=copy.deepcopy(self.template)
        if edit:edit(cfg)
        cfg['review']={'source_sha256':cfg['source']['sha256'],'inventory_sha256':k.inventory_hash(cfg),
                       'reviewer':'synthetic test harness','verdict':'PASS'}
        path=self.base/(name+'.json');path.write_text(json.dumps(cfg))
        return path

    def cli(self,*args):
        return subprocess.run([sys.executable,str(ROOT/'scripts/kangsheng.py'),*map(str,args)],capture_output=True,text=True)

    def test_good_l_shape_and_cache(self):
        job=self.job('good');out=self.base/'good'
        first=self.cli('build',job,'--output',out)
        self.assertEqual(first.returncode,0,first.stderr)
        report=json.loads((out/'audit.json').read_text())
        self.assertEqual(report['source_unplaced_technical_ink_pixels'],0)
        self.assertTrue(all(g['pass'] for g in report['checks']))
        again=self.cli('build',job,'--output',self.base/'good-again','--cache',out/'.cache')
        self.assertEqual(again.returncode,0,again.stderr)
        self.assertTrue(json.loads(again.stdout)['cache_hit'])
        verified=self.cli('verify',job,out/'drawing.pdf')
        self.assertEqual(verified.returncode,0,verified.stderr)
        self.assertFalse(json.loads(verified.stdout)['release_ready'])

    def test_missing_content_rejected(self):
        job=self.job('missing',lambda c:c['groups'][0]['clips'].pop())
        res=self.cli('build',job,'--output',self.base/'missing')
        self.assertNotEqual(res.returncode,0)
        report=json.loads((self.base/'missing/audit.json').read_text())
        self.assertGreater(report['source_unplaced_technical_ink_pixels'],0)
        self.assertFalse((self.base/'missing/drawing.pdf').exists())

    def test_model_conflict_rejected(self):
        job=self.job('model-conflict',lambda c:c['identity'].update(observed_model='MODEL-XR190'))
        res=self.cli('build',job,'--output',self.base/'conflict')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('Identity conflict',res.stderr)

    def test_parts_do_not_cross_models(self):
        job=self.job('parts-conflict',lambda c:c['identity']['observed_parts'].append('X-3P-R190'))
        res=self.cli('build',job,'--output',self.base/'parts-conflict')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('Part identity conflict',res.stderr)

    def test_stale_inventory_rejected(self):
        job=self.job('stale');cfg=json.loads(job.read_text());cfg['groups'][0]['dst'][0]+=1;job.write_text(json.dumps(cfg))
        res=self.cli('build',job,'--output',self.base/'stale')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('Inventory changed',res.stderr)

    def test_actual_ink_overlap_rejected(self):
        def edit(c):
            other=copy.deepcopy(c['groups'][0]);other['id']='duplicate';c['groups'].append(other)
        job=self.job('overlap',edit)
        res=self.cli('build',job,'--output',self.base/'overlap')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('groups overlap',res.stderr)

    def test_source_hash_conflict_rejected(self):
        job=self.job('hash',lambda c:c['source'].update(sha256='0'*64))
        res=self.cli('build',job,'--output',self.base/'hash')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('Source hash changed',res.stderr)

    def test_legitimate_model_spaces_preserved(self):
        def edit(c):
            c['identity']['expected_model']='MODEL-X R125'
            c['fields']['model']='MODEL-X R125'
        job=self.job('legitimate-space',edit)
        cfg,*_=k.read_manifest(job)
        self.assertEqual(cfg['fields']['model'],'MODEL-X R125')

    def test_dimensions_must_keep_source_scale(self):
        job=self.job('bad-scale',lambda c:c['groups'][0].update(scale=1.1))
        res=self.cli('build',job,'--output',self.base/'bad-scale')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('scale must remain 1.0',res.stderr)

    def test_existing_artifact_directory_rejected(self):
        job=self.job('stale-output');out=self.base/'stale-output';out.mkdir()
        prior=out/'drawing.pdf';prior.write_bytes(b'previous-file-do-not-delete')
        res=self.cli('build',job,'--output',out)
        self.assertNotEqual(res.returncode,0)
        self.assertIn('prior artifacts',res.stderr)
        self.assertEqual(prior.read_bytes(),b'previous-file-do-not-delete')

    def test_added_technical_digit_rejected(self):
        job=self.job('tampering');out=self.base/'tampering'
        result=self.cli('build',job,'--output',out)
        self.assertEqual(result.returncode,0,result.stderr)
        d=fitz.open(out/'drawing.pdf')
        d[0].insert_text((172,116),'999',fontsize=12,color=k.BLUE)
        edited=out/'edited.pdf';d.save(edited)
        checked=self.cli('verify',job,edited)
        self.assertNotEqual(checked.returncode,0)
        report=json.loads((out/'verify.json').read_text())
        self.assertGreater(max(c['unexpected_ink_pixels'] for c in report['checks']),0)

    def test_warm_cache_does_not_skip_page_count(self):
        cfg=copy.deepcopy(self.template)
        k.cached_source(cfg,self.source,self.base/'pagecount-cache')
        cfg['source']['expected_pages']=2
        with self.assertRaisesRegex(ValueError,'Page count changed'):
            k.cached_source(cfg,self.source,self.base/'pagecount-cache')

    def test_title_cell_overflow_rejected(self):
        job=self.job('long-title',lambda c:c['fields'].update(title='CONNECTORCONNECTOR'))
        res=self.cli('build',job,'--output',self.base/'long-title')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('title overflows',res.stderr)

    def test_unmapped_gray_parameter_rejected(self):
        gray=self.base/'source-gray.pdf';d=fitz.open(self.source)
        d[0].insert_text((305,350),'RADIUS 1.50 +/-0.05',fontsize=10,color=(.7,.7,.7));d.save(gray)
        def edit(c):c['source'].update(path=str(gray),sha256=k.digest(gray))
        job=self.job('gray',edit)
        res=self.cli('build',job,'--output',self.base/'gray')
        self.assertNotEqual(res.returncode,0)
        report=json.loads((self.base/'gray/audit.json').read_text())
        self.assertGreater(report['source_unplaced_technical_ink_pixels'],0)

    def test_non_ascii_footer_rejected_instead_of_silent_glyph_loss(self):
        job=self.job('footer',lambda c:c['fields'].update(scale_text='3：1',unit='毫米'))
        res=self.cli('build',job,'--output',self.base/'footer')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('unsupported glyphs',res.stderr)

    def test_color_classification(self):
        self.assertEqual(k.classify_color((1,1,1)),(1,1,1))
        self.assertEqual(k.classify_color((.4,.4,.4)),k.BLUE)
        self.assertEqual(k.classify_color((0,0,1)),k.BLUE)
        self.assertEqual(k.classify_color((1,0,0)),(217/255,154/255,0))

if __name__=='__main__':unittest.main()
