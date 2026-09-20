"""Model integration, release gating and legacy compatibility; temporary data only."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Store


class BackgroundWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'data')
        self.exp = self.store.create('Synthetic', 2, 1)
        for role, values in [('pho',(180,160)),('total',(160,160))]:
            pixels=np.full((48,72),200,np.uint8)
            for i,value in enumerate(values): pixels[22:28,12+30*i:20+30*i]=value
            out=io.BytesIO(); Image.fromarray(pixels).save(out,format='PNG')
            self.store.upload(self.exp,role,role+'.png',out.getvalue())
        new=copy.deepcopy(self.exp)
        for role in ('pho','total'):
            new['settings'][role]['region']={'x':0,'y':0,'w':72,'h':48}
            new['rois'][role]=[{'sampleId':s['id'],'band':{'x':12+30*i,'y':22,'w':8,'h':6},'background':{'x':12+30*i,'y':4,'w':8,'h':6},'confirmed':False} for i,s in enumerate(new['samples'])]
        self.store.update(self.exp,new)
        self.confirm()
        self.baseline=copy.deepcopy(self.store.results(self.exp))

    def tearDown(self): self.tmp.cleanup()

    def confirm(self):
        new=copy.deepcopy(self.exp)
        for role in ('pho','total'):
            for roi in new['rois'][role]: roi['confirmed']=True
        self.store.update(self.exp,new)

    def preview(self,role='pho',sigma=2.5):
        self.store.process_background(self.exp,role,{'action':'preview','params':{'clipSigma':sigma}})
        return self.exp['background'][role]['preview']

    def apply(self,role='pho'):
        item=self.exp['background'][role]['preview']
        self.store.process_background(self.exp,role,{'action':'apply','key':item['key']})

    def test_legacy_default_and_preview_are_numerically_identical(self):
        self.assertNotIn('background',self.exp)
        self.assertEqual([r['ratio'] for r in self.baseline['rows']],[0.5,1.0])
        old_rois=copy.deepcopy(self.exp['rois'])
        self.preview()
        self.assertEqual(self.store.results(self.exp),self.baseline)
        self.assertEqual(self.exp['rois'],old_rois)
        self.assertEqual(self.exp['background']['pho']['mode'],'local')

    def test_apply_invalidates_one_role_and_entire_control_baseline(self):
        self.preview();self.apply()
        self.assertTrue(all(not r['confirmed'] for r in self.exp['rois']['pho']))
        self.assertTrue(all(r['confirmed'] for r in self.exp['rois']['total']))
        result=self.store.results(self.exp)
        self.assertTrue(all(r['ratio'] is None and r['relative'] is None for r in result['rows']))
        self.confirm()
        result=self.store.results(self.exp)
        self.assertAlmostEqual(result['rows'][0]['ratio'],0.5,places=10)
        self.assertAlmostEqual(result['rows'][1]['relative'],2.0,places=10)

    def test_model_ignores_legacy_background_and_never_double_subtracts(self):
        self.preview();self.apply();self.confirm()
        new=copy.deepcopy(self.exp)
        for roi in new['rois']['pho']: roi['background']=copy.deepcopy(roi['band'])
        self.store.update(self.exp,new)
        result=self.store.results(self.exp)
        for row,expected in zip(result['rows'],(960,1920)):
            m=row['pho'];self.assertTrue(m['valid']);self.assertAlmostEqual(m['net'],expected,places=8)
            self.assertAlmostEqual(m['backgroundContribution'],200*48,places=8)
            self.assertAlmostEqual(m['net'],m['backgroundContribution']-m['rawSum'],places=8)
            self.assertEqual(m['backgroundArea'],0)

    def test_new_preview_does_not_replace_formal_model_until_apply(self):
        first=self.preview();self.apply();self.confirm();before=self.store.results(self.exp)
        second=self.preview(sigma=3.5)
        self.assertNotEqual(first['key'],second['key'])
        self.assertEqual(self.exp['background']['pho']['applied']['key'],first['key'])
        self.assertEqual(self.store.results(self.exp),before)
        self.apply()
        self.assertTrue(all(not r['confirmed'] for r in self.exp['rois']['pho']))
        self.assertEqual(self.exp['background']['pho']['applied']['params']['clipSigma'],3.5)

    def test_region_and_polarity_invalidate_model_without_local_fallback(self):
        self.preview();self.apply();self.confirm()
        new=copy.deepcopy(self.exp);new['settings']['pho']['region']['x']=1;new['settings']['pho']['region']['w']=71
        self.store.update(self.exp,new)
        bg=self.exp['background']['pho']
        self.assertEqual(bg['mode'],'model');self.assertIsNone(bg['preview']);self.assertIsNone(bg['applied'])
        self.assertIsNone(self.store.results(self.exp)['rows'][0]['pho']['net'])
        self.preview();self.apply()  # Reapplying from stale applied:null is supported.
        new=copy.deepcopy(self.exp);new['settings']['pho']['polarity']='bright'
        self.store.update(self.exp,new)
        self.assertIsNone(self.exp['background']['pho']['applied'])
        self.assertFalse(self.store.results(self.exp)['controlReady'])

    def test_replacing_source_invalidates_model_and_keeps_old_original(self):
        self.preview();self.apply();old=copy.deepcopy(self.exp['images']['pho'])
        data=self.store.image_path(old).read_bytes()
        self.store.upload(self.exp,'pho','replacement.png',data)
        self.assertEqual(self.store.image_path(old).read_bytes(),data)
        self.assertEqual(self.exp['background']['pho']['mode'],'model')
        self.assertIsNone(self.exp['background']['pho']['applied'])
        self.assertEqual(self.exp['rois']['pho'],[])

    def test_restore_original_values_then_require_reconfirmation(self):
        self.preview();self.apply();self.confirm()
        self.store.process_background(self.exp,'pho',{'action':'restore'})
        result=self.store.results(self.exp)
        self.assertTrue(all(r['ratio'] is None for r in result['rows']))
        for a,b in zip(result['rows'],self.baseline['rows']):self.assertEqual(a['pho'],b['pho'])
        self.confirm();self.assertEqual(self.store.results(self.exp),self.baseline)

    def test_restart_restores_applied_and_preview_with_identical_results(self):
        self.preview();self.apply();self.confirm();self.preview(sigma=3.0)
        before=self.store.results(self.exp)
        reopened=Store(self.store.directory);exp=reopened.get(self.exp['id'])
        self.assertEqual(reopened.results(exp),before)
        self.assertEqual(exp['background'],self.exp['background'])
        self.assertEqual(reopened.state['audit'],[json.loads(x) for x in reopened.audit_path.read_text().splitlines()])

    def test_bad_parameters_stale_key_and_corrupt_cache_never_release(self):
        snapshot=copy.deepcopy(self.exp)
        for sigma in (0,7,float('nan'),True):
            with self.assertRaises(ValueError):self.preview(sigma=sigma)
        self.assertEqual(self.exp,snapshot)
        item=self.preview()
        with self.assertRaises(ValueError):self.store.process_background(self.exp,'pho',{'action':'apply','key':'0'*64})
        self.apply();self.confirm()
        path=self.store.derived_dir/item['key']/'corrected.npy'
        content=bytearray(path.read_bytes());content[-1]^=1;path.write_bytes(content)
        self.assertTrue(all(r['ratio'] is None for r in self.store.results(self.exp)['rows']))
        with self.assertRaises(ValueError):self.preview()

    def test_arrays_recomputed_from_original_and_files_are_immutable(self):
        first=self.preview();self.apply();self.confirm();second=self.preview(sigma=3.0)
        a=np.load(self.store.derived_dir/first['key']/'corrected.npy')
        b=np.load(self.store.derived_dir/second['key']/'corrected.npy')
        np.testing.assert_allclose(a,b,rtol=0,atol=1e-10)
        self.apply();self.preview(sigma=2.5)
        self.assertEqual(self.exp['background']['pho']['preview']['key'],first['key'])
        for role in ('pho','total'):
            image=self.exp['images'][role]
            self.assertEqual(hashlib.sha256(self.store.image_path(image).read_bytes()).hexdigest(),image['sha256'])


if __name__=='__main__':unittest.main()
