"""Source fields must include outlined CAD glyphs and survive a cold handoff."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
import pymupdf as fitz
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
from source_fields import (sha,crop_hash,semantic_digest,load_source_fields,audit_source_fields)
from dynamic_tolerance import render_dynamic_tolerance
from kangsheng import source_inventory_hash, source_coverage_preflight, render_array

class SourceFieldsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        doc=fitz.open();p=doc.new_page(width=842,height=595)
        p.insert_text((670,510),'MODEL-A Product')
        # CAD outlined glyphs deliberately have no extractable text.
        p.draw_rect([582,495,595,502]);p.insert_text((582,520),'X.X +-0.25')
        doc.save(self.root/'source.pdf');doc.close()
        (self.root/'font.ttf').write_bytes(fitz.Font('cjk').buffer)
        self.schema={'linear_tolerances':[{'tier':'X.','value':'±0.35'},{'tier':'X.X','value':'±0.25'},
                     {'tier':'X.XX','value':'±0.15'},{'tier':'X.XXX','value':'±0.05'}],
                     'angular_tolerances':[],'additional_tolerance_conditions':[]}
        fields={'model':'MODEL-A','title':'Product','unit':'mm'}
        self.review={'reviewer_identifier':'independent-test-review','rows':[{
            'source_sha256':sha(self.root/'source.pdf'),'field_review_status':'PASS_VISUAL_FIELD_COMPARISON',
            'tolerance_schema':self.schema,**fields}]}
        self.write('review.json',self.review)
        self.e={'schema':'kangsheng-source-fields-v1','source_sha256':sha(self.root/'source.pdf'),
                'rotation':0,'pymupdf':fitz.VersionBind,'fields':fields,'tolerance_schema':self.schema,
                'regions':{},'review':{'path':'review.json','sha256':sha(self.root/'review.json')}}
        with fitz.open(self.root/'source.pdf') as d:
            for name,box in [('tolerance',[580,489,660,547]),('identity',[660,490,840,540])]:
                self.e['regions'][name]={'box':box,'raster_sha256':crop_hash(d[0],box)}
        self.write('fields.json',self.e)
        self.cfg={'source':{'path':'source.pdf','sha256':sha(self.root/'source.pdf'),'rotation':0},
                  'assets':{'font':'font.ttf'},'fields':fields,'groups':[{'id':'tol','kind':'tolerance','reviewed_source_extent':[580,489,660,547]}],
                  'source_fields':{'path':'fields.json','sha256':sha(self.root/'fields.json'),'semantic_sha256':semantic_digest(self.e)}}
    def write(self,name,value):
        (self.root/name).write_text(json.dumps(value))
    def load(self):
        return load_source_fields(self.cfg,self.root/'manifest.json')
    def reseal(self):
        self.write('fields.json',self.e)
        self.cfg['source_fields'].update(sha256=sha(self.root/'fields.json'),semantic_sha256=semantic_digest(self.e))
    def output(self,schema=None,model='MODEL-A'):
        d=fitz.open();p=d.new_page(width=842,height=595)
        render_dynamic_tolerance(p,schema or self.schema)
        p.insert_font(fontname='ks-title-font',fontfile=str(self.root/'font.ttf'))
        for xy,text in [((500,554),model),((700,518),'Product'),((764,538),'UNIT:mm')]:
            p.insert_text(xy,text,fontname='ks-title-font',fontsize=8)
        return d
    def test_valid_reviewed_fields_and_actual_pdf(self):
        e=self.load();d=self.output();self.assertTrue(audit_source_fields(d[0],e)['pass']);d.close()
    def test_partial_regex_ledger_fails_even_if_self_resealed(self):
        self.e['tolerance_schema']=copy.deepcopy(self.schema)
        self.e['tolerance_schema']['linear_tolerances']=self.e['tolerance_schema']['linear_tolerances'][1:2]
        self.reseal()
        with self.assertRaisesRegex(ValueError,'omits or changes'):self.load()
    def test_old_one_line_output_fails(self):
        s=copy.deepcopy(self.schema);s['linear_tolerances']=s['linear_tolerances'][1:2]
        d=self.output(s);self.assertFalse(audit_source_fields(d[0],self.load())['pass']);d.close()
    def test_blank_model_fails_even_when_tolerance_complete(self):
        d=self.output(model='');self.assertFalse(audit_source_fields(d[0],self.load())['pass']);d.close()
    def test_source_changed_blocks_before_render(self):
        (self.root/'source.pdf').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'source bytes changed'):self.load()
    def test_another_model_cannot_borrow_values(self):
        self.cfg['fields']=dict(self.cfg['fields'],model='MODEL-B')
        with self.assertRaisesRegex(ValueError,'model differs'):self.load()
    def test_crop_changed_blocks(self):
        self.e['regions']['tolerance']['raster_sha256']='0'*64;self.reseal()
        with self.assertRaisesRegex(ValueError,'fingerprint changed'):self.load()
    def test_ttc_title_font_blocked(self):
        (self.root/'font.ttf').write_bytes(b'ttcf-invalid')
        with self.assertRaisesRegex(ValueError,'TTC collection'):self.load()
    def test_portable_paths_preserve_source_review_digest(self):
        before=source_inventory_hash(self.cfg)
        (self.root/'relocated').mkdir();self.write('relocated/review.json',self.review)
        self.e['review']['path']='relocated/review.json';self.reseal()
        self.assertEqual(before,source_inventory_hash(self.cfg));self.load()
    def test_ledger_runtime_change_requires_review(self):
        self.e['pymupdf']='0.0';self.reseal()
        with self.assertRaisesRegex(ValueError,'runtime differs'):self.load()
    def add_reviewed_region(self):
        with fitz.open(self.root/'source.pdf') as d:
            box=[660,490,730,520]
            region={'box':box,'raster_sha256':crop_hash(d[0],box)}
        self.e['field_regions']={'model':region}
        self.review['rows'][0]['field_regions']=copy.deepcopy(self.e['field_regions'])
        self.write('review.json',self.review)
        self.e['review']['sha256']=sha(self.root/'review.json')
        self.reseal()
    def test_technical_identity_is_accounted_not_excluded(self):
        self.add_reviewed_region();e=self.load()
        cfg=dict(self.cfg,schema_version=2,coverage={'exclude':[]},_source_fields=e)
        with fitz.open(self.root/'source.pdf') as d:
            a=render_array(d[0]);ps=[{'source_box':[580,489,660,547]},
                                   {'source_box':[730,490,840,540]}]
            result=source_coverage_preflight(cfg,d[0],a,ps)
            self.assertEqual(result['unplaced'],0)
            self.assertEqual(result['authorized_fields'][0]['field'],'model')
            cfg.pop('source_fields');cfg.pop('_source_fields')
            self.assertGreater(source_coverage_preflight(cfg,d[0],a,ps)['unplaced'],0)
    def test_unreviewed_identity_region_blocked(self):
        self.add_reviewed_region();self.e['field_regions']['model']['box'][0]-=1;self.reseal()
        with self.assertRaisesRegex(ValueError,'not independently reviewed'):self.load()
    def test_identity_cannot_be_excluded_as_furniture(self):
        self.add_reviewed_region()
        self.cfg['coverage']={'exclude':[{'box':[660,490,840,540]}]}
        with self.assertRaisesRegex(ValueError,'overlaps nontechnical exclusion'):self.load()
    def test_identity_cannot_duplicate_carried_content(self):
        self.add_reviewed_region()
        self.cfg['groups'][0]['clips']=[[660,490,840,540]]
        with self.assertRaisesRegex(ValueError,'duplicates a carried group'):self.load()
