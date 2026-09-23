"""Self-contained fictional PDFs and fake brand assets; no customer data needed."""
import copy
import hashlib
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
    def test_restored_rule_coverage_includes_measured_stroke_caps(self):
        # A line's source coverage is its painted stroke, not just its centreline.
        # The source table uses round caps; excluding the half-width end caps
        # falsely reports original technical rule pixels as unplaced.
        cfg={'groups':[{'id':'table-edge','kind':'table','clips':[[90,90,100,120]],
                        'dst':[200,200],'scale':1,
                        'restored_source_rules':[
                            {'orientation':'vertical','x':100,'y0':100,'y1':120,'width':.72,'line_cap':'round'},
                            {'orientation':'horizontal','y':120,'x0':90,'x1':100,'width':.72,'line_cap':'round'}]}]}
        vertical,horizontal=list(k.restored_rules(cfg))
        self.assertEqual(vertical['source_box'],[99.64,99.64,100.36,120.36])
        self.assertEqual(horizontal['source_box'],[89.64,119.64,100.36,120.36])
        self.assertEqual(vertical['line_cap'],'round')
        with fitz.open() as doc:
            page=doc.new_page(width=k.PAGE[0],height=k.PAGE[1])
            k.draw_restored_rules(page,cfg)
            self.assertEqual([d['lineCap'] for d in page.get_drawings()],[(1,1,1),(1,1,1)])
        cfg['groups'][0]['restored_source_rules'][0].pop('line_cap')
        self.assertEqual(list(k.restored_rules(cfg))[0]['source_box'],[99.64,100.0,100.36,120.0])

    def test_restored_table_edge_cannot_double_paint_source_stroke(self):
        cfg=copy.deepcopy(self.template)
        table=next(g for g in cfg['groups'] if g['kind']=='table')
        table['clips']=[[420,60,579.8,120]]
        table['reviewed_source_extent']=[420,60,580.5,120]
        table['restored_source_rules']=[{'orientation':'vertical','x':580,
            'y0':65,'y1':115,'width':1,'line_cap':'round','color':'blue'}]
        job=self.job('double-border',lambda c:c.update(cfg))
        with self.assertRaisesRegex(ValueError,'duplicates source stroke'):
            k.read_manifest(job,False)

    def test_restored_vector_audit_rejects_duplicate_table_edge(self):
        cfg={'groups':[{'id':'part-table','kind':'table','clips':[[10,10,90,100]],
            'dst':[100,100],'scale':1,'restored_source_rules':[{'orientation':'vertical',
            'x':90,'y0':10,'y1':100,'width':.72,'line_cap':'round','color':'blue'}]}]}
        with fitz.open() as doc:
            page=doc.new_page(width=k.PAGE[0],height=k.PAGE[1])
            k.draw_restored_rules(page,cfg)
            one=k.restored_rule_vector_checks(page,cfg)
            self.assertEqual(one[0]['visible_rule_count'],1)
            self.assertTrue(one[0]['pass'])
            k.draw_restored_rules(page,cfg)
            two=k.restored_rule_vector_checks(page,cfg)
            self.assertEqual(two[0]['visible_rule_count'],2)
            self.assertFalse(two[0]['pass'])

    def test_clipped_source_vector_is_not_counted_as_visible_duplicate(self):
        source=fitz.open();s=source.new_page(width=200,height=200)
        s.draw_line((100,20),(100,180),color=k.BLUE,width=1,lineCap=1)
        cfg={'groups':[{'id':'part-table','kind':'table','clips':[[20,10,99.4,190]],
            'dst':[20,10],'scale':1,'restored_source_rules':[{'orientation':'vertical',
            'x':100,'y0':20,'y1':180,'width':1,'line_cap':'round','color':'blue'}]}]}
        with fitz.open() as out:
            page=out.new_page(width=200,height=200)
            page.show_pdf_page(fitz.Rect(20,10,99.4,190),source,0,
                               clip=fitz.Rect(20,10,99.4,190))
            for i in range(12):page.draw_line((1,i+1),(2,i+1),color=k.BLUE)
            k.draw_restored_rules(page,cfg)
            check=k.restored_rule_vector_checks(page,cfg)[0]
            self.assertEqual(check['raw_matching_paths'],2)
            self.assertEqual(check['masked_source_paths'],1)
            self.assertEqual(check['visible_rule_count'],1)
            self.assertTrue(check['pass'])
        source.close()

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
        cfg['identity']['record_id']=name
        if edit:edit(cfg)
        cfg['identity'].setdefault('record_id',name)
        cfg['review']={'source_sha256':cfg['source']['sha256'],'source_inventory_sha256':k.source_inventory_hash(cfg),
                       'reviewer':'synthetic source inventory reviewer','reviewer_role':'source_inventory_reviewer',
                       'reviewer_run_id':'source-run-'+name,
                       'coverage_basis':'uncropped-full-sheet','full_page_reviewed':True,'verdict':'PASS'}
        path=self.base/(name+'.json');path.write_text(json.dumps(cfg))
        return path

    def cli(self,*args):
        argv=list(map(str,args))
        if argv[0] in {'draft','build'}:
            argv += ['--control-root',str(self.base/'control')]
        elif argv[0]=='batch' and '--control-root' not in argv:
            argv += ['--control-root',argv[argv.index('--output-root')+1]]
        return subprocess.run([sys.executable,str(ROOT/'scripts/kangsheng.py'),*argv],capture_output=True,text=True)

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

    def english_reflow_fixture(self,name):
        """Synthetic source, distinct approved layout and per-source field map."""
        source=self.base/(name+'-source.pdf')
        with fitz.open(self.source) as doc:
            page=doc[0]
            page.draw_rect(fitz.Rect(300,400,378,460),color=(0,0,0),width=.6)
            page.insert_text((303,410),'SOURCE TOLERANCE',fontsize=6)
            for i,(tier,value) in enumerate([('X.','±0.35'),('X.X','±0.25'),
                                            ('X.XX','±0.15'),('X.XXX','±0.05')]):
                y=421+i*9
                page.insert_text((303,y),tier,fontsize=6,fontname='cour')
                page.draw_line((334,y-3),(339,y-3),width=.4)
                page.draw_line((336.5,y-5.5),(336.5,y-.5),width=.4)
                page.draw_line((334,y-.2),(339,y-.2),width=.4)
                page.insert_text((342,y),value[1:],fontsize=6,fontname='cour')
            page.draw_circle((304,484),4,color=(0,0,0),width=.6)
            page.draw_line((312,480),(324,482),color=(0,0,0),width=.6)
            page.draw_line((312,488),(324,486),color=(0,0,0),width=.6)
            page.draw_line((312,480),(312,488),color=(0,0,0),width=.6)
            page.draw_line((324,482),(324,486),color=(0,0,0),width=.6)
            # The supplier source is outlined in this fixture, as in the
            # real samples, so imported font resources cannot alter ± glyphs.
            svg=page.get_svg_image(text_as_path=True)
            with fitz.open('svg',svg.encode()) as vector:
                with fitz.open('pdf',vector.convert_to_pdf()) as outlined:
                    outlined.save(source)
        cfg=copy.deepcopy(self.template)
        cfg['identity']['record_id']=name
        cfg['source'].update(path=str(source),sha256=k.digest(source))
        cfg['fields'].pop('no_tolerance_block_reason')
        cfg['groups'] += [
            {'id':'original-tolerance','kind':'tolerance','clips':[[298,398,380,462]],
             'reviewed_source_extent':[298,398,380,462],'dst':[400,493],'scale':1},
            {'id':'original-projection','kind':'projection','clips':[[299,479,325,489]],
             'reviewed_source_extent':[299,479,325,489],'dst':[784,549],'scale':1}]
        lines=['X.       ±0.35','X.X     ±0.25','X.XX   ±0.15','X.XXX ±0.05']
        layout={'source_sha256':k.digest(source),'fields':{'model':cfg['fields']['model'],
                 'unit':'mm','tolerances':lines}}
        layout_path=self.base/(name+'-layout.json');layout_path.write_text(json.dumps(layout))
        approved=self.base/(name+'-approved.pdf')
        with fitz.open() as doc:
            page=doc.new_page(width=k.PAGE[0],height=k.PAGE[1])
            page.insert_image(page.rect,filename=cfg['assets']['background'])
            fields=dict(cfg['fields'],tolerances=lines)
            k.draw_frame_and_title(page,fields,cfg['assets'],tolerance_mode='legacy')
            doc.save(approved)
        with fitz.open(source) as doc:
            samples=doc[0].get_pixmap(matrix=fitz.Matrix(6,6),
                clip=fitz.Rect(298,398,380,462),alpha=False).samples
        mapping={'record_id':name,'model':cfg['fields']['model'],
            'source_pdf':str(source),'source_sha256':k.digest(source),
            'source_tolerance_box':[298,398,380,462],
            'source_tolerance_6x_samples_sha256':hashlib.sha256(samples).hexdigest(),
            'source_header':'未注公差 TOOLERANCE',
            'source_projection_label':'视图方法 PROJECTION','source_unit':'mm',
            'rows':[{'tier':tier,'value':value} for tier,value in
                    [('X.','±0.35'),('X.X','±0.25'),('X.XX','±0.15'),('X.XXX','±0.05')]],
            'approved_layout_path':str(layout_path),'approved_layout_sha256':k.digest(layout_path),
            'approved_pdf':str(approved),'approved_pdf_sha256':k.digest(approved),
            'authorized_heading':['UNLESS OTHERWISE','SPECIFIED, TOLERANCE:'],
            'authorized_internal_grid':False,
            'projection_symbol':'source_projection_group_to_bottom_right',
            'source_inventory_complete':True,'additional_tolerance_conditions':[]}
        field_report=self.base/(name+'-independent-field-report.json')
        field_report.write_text(json.dumps({'reviewer_identifier':'synthetic-independent-fixture',
            'rows':[{'record_id':name,'source_sha256':k.digest(source),
                     'source_tolerance_fields':['X. ±0.35','X.X ±0.25','X.XX ±0.15','X.XXX ±0.05'],
                     'tolerance_values_status':'PASS_VISUAL_FIELD_COMPARISON',
                     'unit_status':'PASS','extra_conditions_status':'PASS_VISUAL_INSPECTION'}]}))
        mapping['field_review_report']=str(field_report)
        mapping['field_review_report_sha256']=k.digest(field_report)
        map_path=self.base/(name+'-source-map.json');map_path.write_text(json.dumps(mapping))
        cfg['approved_tolerance_reflow']={'source_map_path':str(map_path),
            'source_map_sha256':k.digest(map_path),'layout_path':str(layout_path),
            'layout_sha256':k.digest(layout_path),'approved_pdf_path':str(approved),
            'approved_pdf_sha256':k.digest(approved)}
        manifest=self.base/(name+'-manifest.json');manifest.write_text(json.dumps(cfg))
        return cfg,manifest,map_path

    def test_approved_english_reflow_rejects_wrong_source_fields_and_extra_conditions(self):
        cfg,manifest,map_path=self.english_reflow_fixture('english-fields')
        k.read_manifest(manifest,False)
        original=json.loads(map_path.read_text())
        for label,edit,error in [
            ('value',lambda x:x['rows'][0].update(value='±0.36'),'template values'),
            ('sign',lambda x:x['rows'][0].update(value='+0.35'),'sign, digits'),
            ('model',lambda x:x.update(model='OTHER MODEL'),'source/model/record'),
            ('extra',lambda x:x.update(additional_tolerance_conditions=['angle ±1°']),'Additional condition inside the source tolerance table'),
            ('incomplete',lambda x:x.update(source_inventory_complete=False),'inventory is not declared complete')]:
            with self.subTest(label=label):
                changed=copy.deepcopy(original);edit(changed);map_path.write_text(json.dumps(changed))
                cfg['approved_tolerance_reflow']['source_map_sha256']=k.digest(map_path)
                manifest.write_text(json.dumps(cfg))
                with self.assertRaisesRegex(ValueError,error):k.read_manifest(manifest,False)
        map_path.write_text(json.dumps(original))

    def test_approved_english_reflow_checks_actual_pdf_and_other_groups(self):
        cfg,manifest,_=self.english_reflow_fixture('english-output')
        cfg,_,source,assets=k.read_manifest(manifest,False)
        neutral,colored,_,_=k.cached_source(cfg,source,self.base/'english-cache')
        with fitz.open(neutral) as source_doc:
            source_pixels=k.render_array(source_doc[0])
            ps,quality=k.check_geometry(cfg,source_doc[0],source_pixels)
        def audit_for(items,suffix):
            doc,_=k.compose_document(cfg,colored,assets,items)
            path=self.base/('english-output-'+suffix+'.pdf');doc.save(path);doc.close()
            return k.make_audit(cfg,neutral,colored,path,ps,assets,source_pixels,
                                source_quality=quality)
        good=audit_for(ps,'good')
        self.assertTrue(good['pass'],good['approved_tolerance_reflow'])
        self.assertTrue(good['approved_tolerance_reflow']['pass'])
        self.assertFalse(next(c for c in good['checks'] if c['id']=='original-tolerance')['pass'])
        without_view=audit_for([p for p in ps if p['id']!='view'],'without-view')
        self.assertFalse(without_view['pass'])
        self.assertFalse(without_view['global_page_match_pass'])
        source_cfg=copy.deepcopy(cfg);source_cfg.pop('approved_tolerance_reflow')
        source_doc,_=k.compose_document(source_cfg,colored,assets,ps)
        source_mode=self.base/'english-output-original-table.pdf';source_doc.save(source_mode);source_doc.close()
        swapped=k.make_audit(cfg,neutral,colored,source_mode,ps,assets,source_pixels,
                             source_quality=quality)
        self.assertFalse(swapped['pass'])
        self.assertFalse(swapped['approved_tolerance_reflow']['pass'])
        good_path=self.base/'english-output-good.pdf'
        wrong=self.base/'english-output-wrong-sign.pdf'
        with fitz.open(good_path) as doc:
            doc[0].insert_text((408,523),'X. +0.35',fontsize=7,color=k.BLUE)
            doc.save(wrong)
        wrong_audit=k.make_audit(cfg,neutral,colored,wrong,ps,assets,source_pixels,
                                 source_quality=quality)
        self.assertFalse(wrong_audit['pass'])
        self.assertFalse(wrong_audit['approved_tolerance_reflow']['pass'])

    def test_reflow_recipe_changes_when_config_or_assets_change(self):
        cfg,manifest,_=self.english_reflow_fixture('english-recipe')
        base=k.inventory_hash(cfg)
        changed=copy.deepcopy(cfg);changed['fields']['scale_text']='2:1'
        self.assertNotEqual(base,k.inventory_hash(changed))
        asset=self.base/'english-recipe-modified-background.png'
        asset.write_bytes(Path(cfg['assets']['background']).read_bytes()+b'\0')
        changed=copy.deepcopy(cfg);changed['assets']['background']=str(asset)
        self.assertNotEqual(base,k.inventory_hash(changed))

    def test_uniform_view_scale_requires_approved_geometry_and_nts(self):
        cfg=copy.deepcopy(self.template)
        cfg['identity']['record_id']='scale-fixture'
        cfg['fields']['scale_text']='NTS'
        cfg['groups'][0]['scale']=1.05
        layout={'source_sha256':cfg['source']['sha256'],'output':str(self.source),
                'output_sha256':k.digest(self.source),'placements':list(k.placements(cfg))}
        layout_path=self.base/'scale-fixture-approved-layout.json'
        layout_path.write_text(json.dumps(layout))
        cfg['approved_uniform_view_scales']={'layout_path':str(layout_path),
                                              'layout_sha256':k.digest(layout_path)}
        job=self.base/'scale-fixture.json';job.write_text(json.dumps(cfg))
        k.read_manifest(job,False)
        changed=copy.deepcopy(cfg);changed['groups'][0]['scale']=1.06
        job.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError,'differs from source-bound approved placement'):
            k.read_manifest(job,False)
        changed=copy.deepcopy(cfg);changed['fields']['scale_text']='3:1'
        job.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError,'require SCALE: NTS'):
            k.read_manifest(job,False)
        changed=copy.deepcopy(cfg);changed['groups'][0]['scale_x']=1.05
        job.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError,'Anisotropic or hidden transform'):
            k.read_manifest(job,False)
        pcb=copy.deepcopy(cfg);pcb['groups'][0]['kind']='pcb';pcb['groups'][0]['id']='pcb'
        pcb_layout=copy.deepcopy(layout)
        for item in pcb_layout['placements']:
            if item['id']=='view':item['id']='pcb';item['kind']='pcb'
        layout_path.write_text(json.dumps(pcb_layout))
        pcb['approved_uniform_view_scales']['layout_sha256']=k.digest(layout_path)
        job.write_text(json.dumps(pcb))
        k.read_manifest(job,False)
        pcb['groups'][0]['scale']=1.06
        job.write_text(json.dumps(pcb))
        with self.assertRaisesRegex(ValueError,'differs from source-bound approved placement'):
            k.read_manifest(job,False)

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
        self.assertEqual(json.loads(again.stdout)['status'],'REUSED_AUTO_QA_PASS')
        self.assertFalse((self.base/'good-again'/'drawing.pdf').exists())
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
            {'id':'batch-pilot','manifest':str(pilot),'pilot':True},
            {'id':'batch-regular','manifest':str(regular)}]}))
        root=self.base/'batch'
        first=self.cli('batch',spec,'--output-root',root)
        self.assertEqual(first.returncode,0,first.stderr)
        state=json.loads((root/'batch-state.json').read_text())
        self.assertEqual(state['jobs']['batch-pilot']['build']['status'],'PREFLIGHT_FAIL')
        self.assertEqual(state['jobs']['batch-regular']['build']['status'],'BLOCKED_PILOT_GATE')
        self.assertEqual(state['jobs']['batch-pilot']['build']['attempts'],0)
        second=self.cli('batch',spec,'--output-root',root)
        self.assertEqual(second.returncode,0,second.stderr)
        state=json.loads((root/'batch-state.json').read_text())
        self.assertEqual(state['jobs']['batch-pilot']['build']['attempts'],0)

    def test_stage_reuse_missing_artifact_and_short_output(self):
        job=self.job('control-stages')
        draft_dir=self.base/'control-stages-draft'
        first=self.cli('draft',job,'--output',draft_dir)
        self.assertEqual(first.returncode,0,first.stderr)
        first_data=json.loads(first.stdout)
        self.assertEqual(set(first_data),{'product_id','stage','status','seconds','attempts',
                                          'error_category','evidence_path'})
        self.assertEqual(first_data['attempts'],1)
        again=self.cli('draft',job,'--output',self.base/'unused-draft-output')
        self.assertEqual(json.loads(again.stdout)['status'],'REUSED_DRAFT_QA_PASS')
        self.assertEqual(json.loads(again.stdout)['attempts'],1)
        (draft_dir/'draft.pdf').unlink()
        repaired=self.cli('draft',job,'--output',draft_dir)
        self.assertEqual(repaired.returncode,0,repaired.stderr)
        self.assertEqual(json.loads(repaired.stdout)['attempts'],2)
        built=self.cli('build',job,'--output',self.base/'control-stages-build')
        self.assertEqual(built.returncode,0,built.stderr)
        self.assertEqual(json.loads(built.stdout)['attempts'],1)
        self.assertFalse((self.base/'control-stages-build'/'release.json').exists())
        original=job.read_text()
        revoked=json.loads(original);revoked['review']['verdict']='REVIEW'
        job.write_text(json.dumps(revoked))
        denied=self.cli('build',job,'--output',self.base/'unused-revoked-build')
        self.assertNotEqual(denied.returncode,0)
        self.assertIn('Source inventory review is required',denied.stderr)
        cfg=json.loads(original);cfg['groups'][0]['dst'][0]+=1
        job.write_text(json.dumps(cfg))
        rebuilt=self.cli('build',job,'--output',self.base/'control-stages-build-changed')
        self.assertEqual(rebuilt.returncode,0,rebuilt.stderr)
        self.assertEqual(json.loads(rebuilt.stdout)['status'],'AUTO_QA_PASS')
        self.assertEqual(json.loads(rebuilt.stdout)['attempts'],2)
        job.write_text(original)
        reverted=self.cli('build',job,'--output',self.base/'unused-reverted-build')
        self.assertEqual(reverted.returncode,0,reverted.stderr)
        self.assertEqual(json.loads(reverted.stdout)['status'],'REUSED_AUTO_QA_PASS')
        self.assertEqual(json.loads(reverted.stdout)['attempts'],2)
        self.assertTrue((draft_dir/'draft-generation-a1.log').is_file())
        self.assertTrue((draft_dir/'draft-generation-a2.log').is_file())

    def test_two_distinct_drafts_share_short_transaction_ledger(self):
        first = self.job('parallel-first')
        second = self.job('parallel-second')
        control = self.base / 'parallel-control'
        jobs = []
        for name, manifest in [('parallel-first', first), ('parallel-second', second)]:
            jobs.append(subprocess.Popen([sys.executable, str(ROOT/'scripts/kangsheng.py'),
                'draft', str(manifest), '--output', str(self.base/name),
                '--control-root', str(control)], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True))
        responses = [job.communicate(timeout=40) for job in jobs]
        for job, (stdout, stderr) in zip(jobs, responses):
            self.assertEqual(job.returncode, 0, stderr)
            self.assertEqual(json.loads(stdout)['status'], 'DRAFT_QA_PASS')
        state = json.loads((control/'batch-state.json').read_text())
        for name in ('parallel-first', 'parallel-second'):
            self.assertEqual(state['jobs'][name]['draft']['status'], 'DRAFT_QA_PASS')

    def test_two_attempt_ceiling_survives_layout_output_entry_and_retry_flag(self):
        job=self.job('control-limit',lambda c:c['groups'][1].update(dst=[900,900]))
        root=self.base/'shared-control'
        def direct(output):
            return subprocess.run([sys.executable,str(ROOT/'scripts/kangsheng.py'),'build',str(job),
                '--output',str(output),'--control-root',str(root),'--allow-retry'],capture_output=True,text=True)
        first=direct(self.base/'limit-a')
        second=direct(self.base/'limit-b')
        self.assertNotEqual(first.returncode,0)
        self.assertNotEqual(second.returncode,0)
        self.assertEqual(json.loads(second.stdout)['attempts'],2)
        cfg=json.loads(job.read_text());cfg['groups'][1]['dst'][0]+=1
        job.write_text(json.dumps(cfg))
        third=direct(self.base/'limit-c')
        self.assertNotEqual(third.returncode,0)
        self.assertEqual(json.loads(third.stdout)['status'],'NEEDS_REVIEW_RETRY_LIMIT')
        self.assertFalse((self.base/'limit-c').exists())
        spec=self.base/'limit-jobs.json'
        spec.write_text(json.dumps({'jobs':[{'id':'control-limit','manifest':str(job),'pilot':True}]}))
        batch=self.cli('batch',spec,'--output-root',self.base/'limit-batch',
                       '--control-root',root,'--allow-retry')
        self.assertEqual(batch.returncode,0,batch.stderr)
        self.assertEqual(json.loads(batch.stdout)['status'],'NEEDS_REVIEW_RETRY_LIMIT')
        self.assertEqual(len(list((self.base/'limit-batch').glob('control-limit/*'))),0)
        self.assertEqual(len(json.loads((root/'batch-state.json').read_text())['events']),4)
        override=self.cli('batch',spec,'--output-root',self.base/'limit-batch',
                          '--control-root',root,'--max-attempts','99')
        self.assertNotEqual(override.returncode,0)
        reset=self.cli('reset-attempts','--control-root',root,'--record-id','control-limit',
                       '--source-sha256',k.digest(self.source),'--stage','build',
                       '--operator','synthetic-reviewer','--reason','Synthetic test reset with audit')
        self.assertEqual(reset.returncode,0,reset.stderr)
        state=json.loads((root/'batch-state.json').read_text())
        self.assertEqual(state['runs'][f'control-limit|{k.digest(self.source)}|build']['attempts'],0)
        self.assertEqual(state['events'][-1]['status'],'MANUAL_RESET')
        self.assertEqual(state['events'][-1]['previous']['attempts'],2)

    def test_legacy_ledger_stops_without_rewrite_and_identity_mismatch_preflights(self):
        job=self.job('control-legacy')
        root=self.base/'legacy-control';root.mkdir()
        old=b'{"schema_version":1,"jobs":{"KEEP":{"attempts":2}}}\n'
        (root/'batch-state.json').write_bytes(old)
        res=subprocess.run([sys.executable,str(ROOT/'scripts/kangsheng.py'),'draft',str(job),
            '--output',str(self.base/'legacy-draft'),'--control-root',str(root)],capture_output=True,text=True)
        self.assertNotEqual(res.returncode,0)
        self.assertIn('migration is not supported',res.stderr)
        self.assertEqual((root/'batch-state.json').read_bytes(),old)
        spec=self.base/'mismatch-jobs.json'
        spec.write_text(json.dumps({'jobs':[{'id':'wrong-record','manifest':str(job),'pilot':True}]}))
        out=self.base/'identity-batch'
        mismatch=self.cli('batch',spec,'--output-root',out)
        self.assertEqual(mismatch.returncode,0,mismatch.stderr)
        self.assertEqual(json.loads(mismatch.stdout)['status'],'PREFLIGHT_FAIL')
        state=json.loads((out/'batch-state.json').read_text())
        self.assertEqual(state['runs'],{})

    def test_batch_pilot_requires_independent_release_before_expansion(self):
        pilot=self.job('gate-pilot');regular=self.job('gate-regular')
        spec=self.base/'gate-jobs.json'
        spec.write_text(json.dumps({'jobs':[{'id':'gate-pilot','manifest':str(pilot),'pilot':True},
                                          {'id':'gate-regular','manifest':str(regular)}]}))
        root=self.base/'gate-root'
        first=self.cli('batch',spec,'--output-root',root)
        self.assertEqual(first.returncode,0,first.stderr)
        state=json.loads((root/'batch-state.json').read_text())
        self.assertEqual(state['jobs']['gate-pilot']['build']['status'],'AUTO_QA_PASS')
        self.assertEqual(state['jobs']['gate-regular']['build']['status'],'BLOCKED_PILOT_GATE')
        run=Path(state['jobs']['gate-pilot']['build']['run_dir'])
        unreviewed=self.cli('verify',pilot,run/'drawing.pdf')
        self.assertEqual(unreviewed.returncode,0,unreviewed.stderr)
        self.assertFalse((run/'release.json').exists())
        second=self.cli('batch',spec,'--output-root',root)
        self.assertEqual(second.returncode,0,second.stderr)
        self.assertEqual(json.loads((root/'batch-state.json').read_text())['jobs']['gate-regular']['build']['status'],
                         'BLOCKED_PILOT_GATE')
        review=json.loads((run/'final-review-template.json').read_text())
        review.update({'reviewer':'independent reviewer','reviewer_run_id':'gate-final',
                       'full_page_compared':True,'verdict':'PASS','checks':review['required_checks']})
        evidence=run/'review.json';evidence.write_text(json.dumps(review))
        released=self.cli('verify',pilot,run/'drawing.pdf','--review',evidence)
        self.assertEqual(released.returncode,0,released.stderr)
        third=self.cli('batch',spec,'--output-root',root)
        self.assertEqual(third.returncode,0,third.stderr)
        state=json.loads((root/'batch-state.json').read_text())
        self.assertEqual(state['jobs']['gate-pilot']['build']['status'],'RELEASE_READY')
        self.assertEqual(state['jobs']['gate-regular']['build']['status'],'AUTO_QA_PASS')

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
