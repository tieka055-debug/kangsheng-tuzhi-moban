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
        cls.template={'schema_version':2,'source':{'path':str(cls.source),'sha256':k.digest(cls.source),
             'page':1,'rotation':0,'expected_pages':1},'renderer':'auto',
          'identity':{'expected_model':'MODEL-XR125','observed_model':'MODEL-X R125',
             'model_evidence':'Synthetic source header','observed_parts':['X-3P-R125','X-4P-R125'],
             'part_pattern':r'X-[34]P-R125'},
          'fields':{'model':'MODEL-XR125','title':'CONNECTOR','scale_text':'','unit':'mm','sheet':'1/1','tolerances':[],
                    'no_tolerance_block_reason':'Synthetic fixture has no tolerance block'},
          'assets':{'background':str(cls.base/'background.png'),'brand_strip':str(cls.base/'brand-strip.png'),'font':str(font)},
          'groups':[{'id':'view','kind':'view','clips':[[40,80,200,160],[40,160,150,180]],'reviewed_source_extent':[40,80,200,180],'dst':[60,100],'scale':1},
             {'id':'table','kind':'table','clips':[[420,60,580,120]],'reviewed_source_extent':[420,60,580,120],'dst':[574,50],'scale':1},
             {'id':'specs','kind':'performance','clips':[[420,220,590,285]],'reviewed_source_extent':[420,220,590,285],'dst':[570,250],'scale':1}],
          'coverage':{'mode':'full-page-minus-exclusions','exclude':[{'box':[20,10,400,60],
             'kind':'replaced_title_field','reason':'Source synthetic title copied via reviewed title field'}]}}

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def job(self,name,edit=None):
        cfg=copy.deepcopy(self.template)
        if edit:edit(cfg)
        cfg['review']={'source_sha256':cfg['source']['sha256'],'source_inventory_sha256':k.source_inventory_hash(cfg),
                       'reviewer':'synthetic source inventory reviewer','reviewer_role':'source_inventory_reviewer',
                       'reviewer_run_id':'source-run-'+name,
                       'coverage_basis':'uncropped-full-sheet','full_page_reviewed':True,'verdict':'PASS'}
        path=self.base/(name+'.json');path.write_text(json.dumps(cfg))
        return path

    def cli(self,*args):
        return subprocess.run([sys.executable,str(ROOT/'scripts/kangsheng.py'),*map(str,args)],capture_output=True,text=True)

    def footer_fixture(self,name,restored=False):
        """One original tolerance table and a small projection symbol."""
        source=self.base/(name+'-source.pdf')
        with fitz.open(self.source) as doc:
            page=doc[0]
            page.draw_rect(fitz.Rect(300,400,378,460),color=(0,0,0),width=.6)
            page.draw_line((300,417),(378,417),color=(0,0,0),width=.6)
            page.insert_text((305,412),'ORIGINAL LIMITS',fontsize=7)
            page.insert_text((305,432),'+/- 0.10',fontsize=7)
            page.insert_text((305,447),'+/- 0.25',fontsize=7)
            page.draw_circle((304,484),4,color=(0,0,0),width=.6)
            page.draw_line((312,480),(324,482),color=(0,0,0),width=.6)
            page.draw_line((312,488),(324,486),color=(0,0,0),width=.6)
            page.draw_line((312,480),(312,488),color=(0,0,0),width=.6)
            page.draw_line((324,482),(324,486),color=(0,0,0),width=.6)
            doc.save(source)
        cfg=copy.deepcopy(self.template)
        cfg['source'].update(path=str(source),sha256=k.digest(source))
        cfg['fields'].pop('no_tolerance_block_reason')
        tolerance={'id':'original-tolerance','kind':'tolerance',
                   'clips':[[298,398,380,462]],'reviewed_source_extent':[298,398,380,462],
                   'dst':[404,498],'scale':1}
        if restored:
            tolerance.update(clips=[[298,398,378,460]],reviewed_source_extent=[298,398,378.3,460.3],
                # Flat-capped closing rules span the original rectangle's
                # complete outside edges, including its joined corner pixels.
                restored_source_rules=[{'orientation':'vertical','x':378,'y0':399.7,'y1':460.3,'width':.6,'color':'blue'},
                                       {'orientation':'horizontal','y':460,'x0':299.7,'x1':378.3,'width':.6,'color':'blue'}])
        cfg['groups'] += [tolerance,{'id':'original-projection','kind':'projection',
            'clips':[[299,479,325,489]],'reviewed_source_extent':[299,479,325,489],
            'dst':[784,549],'scale':1}]
        return cfg,source

    def test_v2_original_tolerance_and_projection_use_only_dedicated_cells(self):
        cfg,source=self.footer_fixture('source-footer')
        cfg['assets']['brand_strip']=str(ROOT/'assets/brand-strip.png')
        job=self.job('source-footer',lambda c:c.update(cfg));out=self.base/'source-footer'
        result=self.cli('build',job,'--output',out)
        self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads((out/'audit.json').read_text())
        self.assertTrue(report['pass'])
        self.assertTrue(all(c['pass'] for c in report['checks'] if c['id'].startswith('original-')))
        self.assertTrue(all(count==1 for count in report['frame_field_occurrences'].values()))
        self.assertEqual(report['brand_profile']['profile'],'approved-native-original')
        self.assertFalse(report['brand_profile']['normal_minimum_dpi_met'])
        self.assertEqual(report['brand_effective_dpi'],127.1)
        self.assertIn('rows 14-18',report['brand_profile']['approved_source'])
        with fitz.open(out/'drawing.pdf') as doc:
            text=doc[0].get_text(clip=fitz.Rect(k.TOLERANCE_BOX))
            self.assertNotIn('UNLESS OTHERWISE',text)
            self.assertNotIn('SPECIFIED, TOLERANCE:',text)
        checked=self.cli('verify',job,out/'drawing.pdf')
        self.assertEqual(checked.returncode,0,checked.stderr)

    def test_source_tolerance_cannot_drift_outside_its_cell(self):
        cfg,source=self.footer_fixture('tolerance-drift')
        with fitz.open(source) as doc:
            pixels=k.render_array(doc[0])
            for dst in ([402,420],[410,498],[396,498]):
                with self.subTest(dst=dst):
                    cfg['groups'][-2]['dst']=dst
                    with self.assertRaisesRegex(ValueError,'Source tolerance must fit its dedicated cell'):
                        k.check_geometry(cfg,doc[0],pixels)

    def test_projection_cannot_use_kind_to_cover_title_or_another_area(self):
        cfg,source=self.footer_fixture('projection-drift')
        with fitz.open(source) as doc:
            pixels=k.render_array(doc[0])
            for dst in ([700,500],[770,549],[360,360]):
                with self.subTest(dst=dst):
                    cfg['groups'][-1]['dst']=dst
                    with self.assertRaisesRegex(ValueError,'Projection must fit its bottom-right cell'):
                        k.check_geometry(cfg,doc[0],pixels)

    def test_other_group_still_cannot_enter_tolerance_cell(self):
        cfg,source=self.footer_fixture('non-tolerance-overlap')
        cfg['groups'][-2]['kind']='note'
        with fitz.open(source) as doc:
            with self.assertRaisesRegex(ValueError,'Title/tolerance overlap'):
                k.check_geometry(cfg,doc[0])

    def test_v2_tolerance_group_is_unique(self):
        cfg,_=self.footer_fixture('duplicate-tolerance')
        extra=copy.deepcopy(cfg['groups'][-2]);extra['id']='second-tolerance'
        cfg['groups'].append(extra)
        job=self.job('duplicate-tolerance',lambda c:c.update(cfg))
        with self.assertRaisesRegex(ValueError,'one complete source tolerance group'):
            k.read_manifest(job)

    def test_source_tolerance_closing_rules_stay_inside_cell_including_stroke(self):
        cfg,source=self.footer_fixture('tolerance-rules',restored=True)
        job=self.job('tolerance-rules',lambda c:c.update(cfg));out=self.base/'tolerance-rules'
        result=self.cli('build',job,'--output',out)
        self.assertEqual(result.returncode,0,result.stderr)
        cfg['groups'][-2]['dst']=[407.8,498]
        with fitz.open(source) as doc:
            with self.assertRaisesRegex(ValueError,'Restored tolerance rule must fit its dedicated cell'):
                k.check_geometry(cfg,doc[0])

    def test_schema1_keeps_legacy_tolerance_furniture(self):
        cfg=copy.deepcopy(self.template);cfg['schema_version']=1
        cfg['fields']['tolerances']=['+/- 0.10']
        doc,page=k.compose_document(cfg,self.source,cfg['assets'],[])
        try:
            text=page.get_text(clip=fitz.Rect(k.TOLERANCE_BOX))
            self.assertIn('UNLESS OTHERWISE',text)
            self.assertIn('SPECIFIED, TOLERANCE:',text)
            self.assertIn('+/- 0.10',text)
            self.assertEqual(page.get_text().count('CONNECTOR'),1)
            self.assertEqual(page.get_text().count('MODEL-XR125'),1)
        finally:doc.close()

    def test_title_and_model_raster_match_native_font_weight(self):
        # Compare rendered glyphs with an independent, single-fill reference.
        # Text occurrence checks alone miss thick strokes that turn words into
        # ink blobs while leaving the extracted text unchanged.
        cases=[(self.template['fields'],self.template['assets'])]
        cjk=Path('/System/Library/Fonts/STHeiti Medium.ttc')
        if cjk.is_file():
            fields=dict(self.template['fields'],title='电池连接座',model='BC-12系列低R')
            assets=dict(self.template['assets'],font=str(cjk))
            cases.append((fields,assets))
        for fields,assets in cases:
            with self.subTest(font=assets['font']):
                actual=fitz.open();page=actual.new_page(width=k.PAGE[0],height=k.PAGE[1])
                k.draw_frame_and_title(page,fields,assets,tolerance_mode='source')
                expected=fitz.open();reference=expected.new_page(width=k.PAGE[0],height=k.PAGE[1])
                reference.insert_font(fontname='reference',fontfile=assets['font'])
                font=fitz.Font(fontfile=assets['font'])
                for key,box,size in [('title',[696,505,813,524],11),('model',[491,541,635,563],9)]:
                    bounds=fitz.Rect(box);value=fields[key]
                    width=font.text_length(value,fontsize=size)
                    if key=='model' and width>bounds.width-4:
                        size *= (bounds.width-4)/width
                        width=font.text_length(value,fontsize=size)
                    x=bounds.x0+(bounds.width-width)/2
                    y=bounds.y0+(bounds.height+size*.72)/2
                    reference.insert_text((x,y),value,fontname='reference',fontsize=size,color=k.BLUE)
                    actual_pixels=page.get_pixmap(clip=bounds,matrix=fitz.Matrix(4,4),alpha=False)
                    expected_pixels=reference.get_pixmap(clip=bounds,matrix=fitz.Matrix(4,4),alpha=False)
                    self.assertTrue(actual_pixels.samples==expected_pixels.samples,
                                    key+' raster differs from native filled glyphs')
                actual.close();expected.close()

    def test_approved_native_brand_exception_requires_exact_bytes(self):
        original=ROOT/'assets/brand-strip.png'
        self.assertTrue(k.brand_profile(original)['pass'])
        changed=self.base/'brand-modified.png';changed.write_bytes(original.read_bytes()+b'\0')
        unknown=self.base/'brand-unknown.png'
        with fitz.open() as doc:
            page=doc.new_page(width=597,height=92)
            page.insert_text((20,45),'OTHER BRAND',fontsize=16)
            page.get_pixmap().save(unknown)
        for name,path in [('modified',changed),('unknown',unknown)]:
            with self.subTest(name=name):
                profile=k.brand_profile(path)
                self.assertEqual(profile['profile'],'standard')
                self.assertFalse(profile['pass'])
                job=self.job('brand-'+name,lambda c:c['assets'].update(brand_strip=str(path)))
                with self.assertRaisesRegex(ValueError,'Brand strip effective resolution'):
                    k.read_manifest(job)

    def test_good_l_shape_and_cache(self):
        job=self.job('good');out=self.base/'good'
        first=self.cli('build',job,'--output',out)
        self.assertEqual(first.returncode,0,first.stderr)
        report=json.loads((out/'audit.json').read_text())
        self.assertEqual(report['source_unplaced_technical_ink_pixels'],0)
        self.assertTrue(report['global_page_match_pass'])
        self.assertTrue(all(g['pass'] for g in report['checks']))
        for name in ['review-board.png','review-details.png','final-review-template.json',
                     'manifest.snapshot.json','run.json']:
            self.assertTrue((out/name).is_file(),name)
        again=self.cli('build',job,'--output',self.base/'good-again','--cache',out/'.cache')
        self.assertEqual(again.returncode,0,again.stderr)
        self.assertTrue(json.loads(again.stdout)['cache_hit'])
        verified=self.cli('verify',job,out/'drawing.pdf')
        self.assertEqual(verified.returncode,0,verified.stderr)
        self.assertFalse(json.loads(verified.stdout)['release_ready'])

    def test_missing_content_rejected(self):
        def edit(c):
            c['groups'][0]['clips'].pop()
            # Simulate a reviewer-provided extent that is stale/too narrow;
            # full-sheet coverage must still catch the dropped second piece.
            c['groups'][0]['reviewed_source_extent']=[40,80,200,160]
        job=self.job('missing',edit)
        res=self.cli('build',job,'--output',self.base/'missing')
        self.assertNotEqual(res.returncode,0)
        self.assertTrue('Reviewed source extent' in res.stderr or 'Source clip cuts text' in res.stderr)
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

    def test_layout_change_does_not_stale_source_inventory(self):
        job=self.job('layout-only');cfg=json.loads(job.read_text());cfg['groups'][0]['dst'][0]+=1;job.write_text(json.dumps(cfg))
        res=self.cli('build',job,'--output',self.base/'layout-only')
        self.assertEqual(res.returncode,0,res.stderr)

    def test_source_clip_change_stales_inventory(self):
        job=self.job('stale-source');cfg=json.loads(job.read_text());cfg['groups'][0]['id']='view-renamed';job.write_text(json.dumps(cfg))
        res=self.cli('build',job,'--output',self.base/'stale-source')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('Source clips or inventory changed',res.stderr)

    def test_cropped_basis_is_rejected(self):
        job=self.job('cropped-basis')
        cfg=json.loads(job.read_text());cfg['review']['coverage_basis']='operator-crops'
        cfg['review']['inventory_sha256']=k.inventory_hash(cfg);job.write_text(json.dumps(cfg))
        res=self.cli('build',job,'--output',self.base/'cropped-basis')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('uncropped full source page',res.stderr)

    def test_reviewed_table_extent_cannot_be_clipped(self):
        def edit(c):
            c['groups'][1]['clips']=[[420,60,570,120]]
        job=self.job('table-extent',edit)
        res=self.cli('build',job,'--output',self.base/'table-extent')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('Reviewed source extent is not fully placed: table',res.stderr)

    def test_group_cannot_leak_excluded_source_furniture(self):
        def edit(c):
            c['groups'][0]['clips']=[[40,20,200,160]]
            c['groups'][0]['reviewed_source_extent']=[40,20,200,160]
        job=self.job('furniture-leak',edit)
        res=self.cli('build',job,'--output',self.base/'furniture-leak')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('Group enters excluded source furniture: view',res.stderr)

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

    def test_added_ink_in_blank_page_area_rejected(self):
        job=self.job('blank-tamper');out=self.base/'blank-tamper'
        result=self.cli('build',job,'--output',out)
        self.assertEqual(result.returncode,0,result.stderr)
        d=fitz.open(out/'drawing.pdf');d[0].insert_text((350,350),'999',fontsize=12,color=k.BLUE)
        edited=out/'blank-edited.pdf';d.save(edited)
        checked=self.cli('verify',job,edited)
        self.assertNotEqual(checked.returncode,0)
        report=json.loads((out/'verify.json').read_text())
        self.assertGreater(report['global_unexpected_ink_pixels'],0)

    def test_added_ink_in_title_cell_rejected(self):
        job=self.job('title-tamper');out=self.base/'title-tamper'
        result=self.cli('build',job,'--output',out)
        self.assertEqual(result.returncode,0,result.stderr)
        d=fitz.open(out/'drawing.pdf');d[0].insert_text((700,500),'WRONG',fontsize=8,color=k.BLUE)
        edited=out/'title-edited.pdf';d.save(edited)
        checked=self.cli('verify',job,edited)
        self.assertNotEqual(checked.returncode,0)
        report=json.loads((out/'verify.json').read_text())
        self.assertGreater(report['global_unexpected_ink_pixels'],0)

    def test_v2_rejects_operator_narrowed_coverage(self):
        def edit(c):
            c['coverage']['include']=[c['groups'][0]['clips'][0]]
        job=self.job('narrow-coverage',edit)
        res=self.cli('build',job,'--output',self.base/'narrow-coverage')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('complete rotated page',res.stderr)

    def test_unreadable_effective_font_rejected(self):
        job=self.job('tiny-font',lambda c:c['groups'][2].update(scale=.5))
        res=self.cli('build',job,'--output',self.base/'tiny-font')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('Effective technical text below',res.stderr)

    def test_clip_padding_cannot_carry_unreviewed_ink(self):
        def edit(c):
            c['groups'][0]['reviewed_source_extent']=[50,80,200,180]
        job=self.job('padding-ink',edit)
        res=self.cli('build',job,'--output',self.base/'padding-ink')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('outside reviewed source extent',res.stderr)

    def test_partial_source_word_at_clip_edge_rejected(self):
        def edit(c):
            c['groups'][1]['clips']=[[440,60,580,120]]
            c['groups'][1]['reviewed_source_extent']=[440,60,580,120]
        job=self.job('cut-word',edit)
        res=self.cli('build',job,'--output',self.base/'cut-word')
        self.assertNotEqual(res.returncode,0)
        self.assertIn('Source clip cuts text',res.stderr)

    def test_draft_does_not_require_source_review_and_is_repeatable(self):
        job=self.job('draft');cfg=json.loads(job.read_text());cfg['review']['verdict']='REVIEW';job.write_text(json.dumps(cfg))
        out=self.base/'draft-work'
        first=self.cli('draft',job,'--output',out)
        self.assertEqual(first.returncode,0,first.stderr)
        second=self.cli('draft',job,'--output',out)
        self.assertEqual(second.returncode,0,second.stderr)
        self.assertTrue((out/'draft-board.png').is_file())

    def test_batch_pilot_gate_and_unchanged_failure_are_idempotent(self):
        pilot=self.job('batch-pilot',lambda c:c['identity'].update(observed_model='WRONG'))
        regular=self.job('batch-regular')
        spec=self.base/'jobs.json';spec.write_text(json.dumps({'jobs':[
            {'id':'pilot','manifest':str(pilot),'pilot':True},
            {'id':'regular','manifest':str(regular)}]}))
        root=self.base/'batch'
        first=self.cli('batch',spec,'--output-root',root)
        self.assertEqual(first.returncode,0,first.stderr)
        state=json.loads((root/'batch-state.json').read_text())
        self.assertEqual(state['jobs']['pilot']['status'],'PREFLIGHT_FAIL')
        self.assertEqual(state['jobs']['regular']['status'],'BLOCKED_PILOT_GATE')
        self.assertEqual(state['jobs']['pilot']['attempts'],1)
        second=self.cli('batch',spec,'--output-root',root)
        self.assertEqual(second.returncode,0,second.stderr)
        state=json.loads((root/'batch-state.json').read_text())
        self.assertEqual(state['jobs']['pilot']['attempts'],1)

    def test_final_review_cannot_use_source_reviewer(self):
        job=self.job('review-separation');out=self.base/'review-separation'
        built=self.cli('build',job,'--output',out)
        self.assertEqual(built.returncode,0,built.stderr)
        evidence={'source_sha256':k.digest(self.source),'output_sha256':k.digest(out/'drawing.pdf'),
                  'inventory_sha256':k.inventory_hash(json.loads(job.read_text())),
                  'verdict':'PASS','reviewer':'synthetic source inventory reviewer',
                  'reviewer_role':'independent_final_reviewer','full_page_compared':True,'checks':['full-page']}
        review=out/'review.json';review.write_text(json.dumps(evidence))
        verified=self.cli('verify',job,out/'drawing.pdf','--review',review)
        self.assertNotEqual(verified.returncode,0)
        self.assertIn('cannot self-approve',verified.stderr)

    def test_hash_bound_independent_review_releases_v2(self):
        job=self.job('release');out=self.base/'release'
        built=self.cli('build',job,'--output',out)
        self.assertEqual(built.returncode,0,built.stderr)
        review=json.loads((out/'final-review-template.json').read_text())
        review.update({'reviewer':'independent synthetic reviewer','reviewer_run_id':'final-run-release',
                       'full_page_compared':True,'verdict':'PASS','checks':review['required_checks']})
        evidence=out/'final-review.json';evidence.write_text(json.dumps(review))
        verified=self.cli('verify',job,out/'drawing.pdf','--review',evidence)
        self.assertEqual(verified.returncode,0,verified.stderr)
        self.assertTrue((out/'release.json').is_file())

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

    def test_init_creates_v2_source_map_and_inspection(self):
        manifest=self.base/'init-job.json'
        result=self.cli('init',self.source,'--manifest',manifest,'--model','MODEL-XR125','--rotation','0')
        self.assertEqual(result.returncode,0,result.stderr)
        cfg=json.loads(manifest.read_text())
        self.assertEqual(cfg['schema_version'],2)
        self.assertEqual(cfg['coverage']['mode'],'full-page-minus-exclusions')
        self.assertTrue((self.base/'init-job-source-map.png').is_file())
        inspection=json.loads((self.base/'init-job-inspection.json').read_text())
        self.assertTrue(inspection['text_blocks'])

if __name__=='__main__':unittest.main()
