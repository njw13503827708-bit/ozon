"""Isolated mocked model contracts; these fixtures are not real 1688 evidence."""
import copy
import base64
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

from tests import test_ozon_ecommerce_designer_contract as fixtures
from scripts.production_input_guard import write_source_manifest


class Intelligent1688WorkflowTest(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.product,self.skus=fixtures.make_product(self.root,'P000123',2)
        self.design=fixtures.build_design(self.product,self.skus)
        self.assertEqual([],fixtures.validate_design(self.product,self.design))
        self.environment=patch.dict(os.environ,{'OPENAI_API_KEY':'offline-test-key','OPENAI_TEXT_MODEL':'test-model'})
        self.environment.start();self.addCleanup(self.environment.stop)

    def response(self, design=None):
        return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(design or self.design)}]}],
                'usage':{'input_tokens':10,'output_tokens':20}}

    def test_two_variants_materialize_same_source_without_codex_and_no_repeat_charge(self):
        from scripts.model_production import run_model_stage
        with patch('scripts.run_batch.codex_exec_command',side_effect=AssertionError('customer must not need Codex')):
            result=run_model_stage(self.product,'ecommerce_design',{},transport=lambda *_a:self.response())
            again=run_model_stage(self.product,'ecommerce_design',{},transport=lambda *_a:self.fail('double charge'))
        design=json.loads((self.product/'output/ozon-ecommerce-design.json').read_text(encoding='utf-8'))
        self.assertEqual([s['sku_id'] for s in self.skus],[s['sku_id'] for s in design['sku_plan']])
        self.assertEqual('backend_model_api',design['processing']['model_mode'])
        copy_ru=json.loads((self.product/'output/copy-ru.json').read_text(encoding='utf-8'))
        self.assertEqual(design['listing']['seo_title_ru'],copy_ru['title_ru'])
        self.assertEqual(result,again)
        self.assertTrue((self.product/'output/image-plan.json').is_file())

    def test_missing_key_sends_zero_requests(self):
        from scripts.model_production import run_model_stage
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}), patch('scripts.model_production.generate_document') as provider:
            with self.assertRaises(ValueError):run_model_stage(self.product,'ecommerce_design',{})
            provider.assert_not_called()

    def test_unknown_paid_result_survives_restart_without_replay(self):
        from scripts.model_production import run_model_stage
        def timeout(*_args):raise TimeoutError('after dispatch')
        with self.assertRaises(RuntimeError):run_model_stage(self.product,'ecommerce_design',{},transport=timeout)
        with self.assertRaises(RuntimeError):run_model_stage(self.product,'ecommerce_design',{},transport=lambda *_a:self.fail('replayed unknown'))
        self.assertFalse((self.product/'output/ozon-ecommerce-design.json').exists())

    def test_fabricated_sku_and_demo_never_enter_production(self):
        from scripts.model_production import run_model_stage
        design=copy.deepcopy(self.design);design['sku_plan'][0]['sku_id']='invented-combination'
        with self.assertRaises(ValueError):run_model_stage(self.product,'ecommerce_design',{},transport=lambda *_a:self.response(design))
        demo,_=fixtures.make_product(self.root,'P080001',1)
        with patch('scripts.model_production.generate_document') as provider:
            with self.assertRaises(RuntimeError):run_model_stage(demo,'ecommerce_design',{})
            provider.assert_not_called()

    def test_conditional_bulk_quote_is_not_used_as_unconditional_purchase_price(self):
        from scripts.model_production import run_model_stage
        path=self.product/'input/source.json';source=json.loads(path.read_text(encoding='utf-8'))
        source['skus'][0]['source_data']={'price_quantity_threshold':200};source['skus'][0]['purchase_price']=46
        path.write_text(json.dumps(source),encoding='utf-8');write_source_manifest(self.product)
        with patch('scripts.model_production.generate_document',side_effect=AssertionError('bulk quote must block before paid call')) as provider:
            with self.assertRaisesRegex(RuntimeError,'采购数量'):run_model_stage(self.product,'ecommerce_design',{})
            provider.assert_not_called()

    def test_concurrent_source_edit_preserves_result_but_does_not_project(self):
        from scripts.model_production import run_model_stage
        def changed(*_args):
            path=self.product/'input/source.json'; source=json.loads(path.read_text(encoding='utf-8'));source['title_cn']='changed'
            path.write_text(json.dumps(source),encoding='utf-8');write_source_manifest(self.product)
            return self.response()
        with self.assertRaises(RuntimeError):run_model_stage(self.product,'ecommerce_design',{},transport=changed)
        self.assertFalse((self.product/'output/copy-ru.json').exists())

    def test_restart_after_paid_response_preserves_later_human_design(self):
        from scripts.model_production import run_model_stage
        with patch('scripts.model_production.materialize',side_effect=RuntimeError('crash after response')):
            with self.assertRaises(RuntimeError):run_model_stage(self.product,'ecommerce_design',{},transport=lambda *_a:self.response())
        output=self.product/'output/ozon-ecommerce-design.json';output.write_text('{"human":"unsaved work recovered"}')
        with self.assertRaises(RuntimeError):run_model_stage(self.product,'ecommerce_design',{},transport=lambda *_a:self.fail('double charge'))
        self.assertEqual('{"human":"unsaved work recovered"}',output.read_text(encoding='utf-8'))

    def test_backend_image_worker_does_not_call_codex_or_fake_qc_pass(self):
        from scripts import run_batch
        with (patch('scripts.model_production.run_model_image_slot',return_value={'status':'NEEDS_REVIEW'}) as worker,
              patch.object(run_batch,'codex_exec_command',side_effect=AssertionError('Codex invoked'))):
            result=run_batch.run_single_image_slot(self.product,{'model_runtime':'backend_api'}, {'slot':'sku-1','output_path':'missing.png'},1)
        self.assertEqual('NEEDS_REVIEW',result['status']);worker.assert_called_once()

    def test_actual_image_adapter_and_failed_vision_do_not_grant_qc_pass(self):
        from scripts.model_production import run_model_stage,run_model_image_slot
        buffer=io.BytesIO();Image.new('RGB',(960,1280),'blue').save(buffer,format='PNG');image=buffer.getvalue()
        for sku in self.skus:(self.root/sku['local_image_path']).write_bytes(image)
        write_source_manifest(self.product)
        run_model_stage(self.product,'ecommerce_design',{},transport=lambda *_a:self.response())
        plan=json.loads((self.product/'output/image-plan.json').read_text(encoding='utf-8'));slot=plan['main_images'][0]
        checks={key:False for key in ('identity','color','sku','structure','markings','legibility','product_visually_dominant')}
        with patch('scripts.run_batch.codex_exec_command',side_effect=AssertionError('No Codex dependency')):
            result=run_model_image_slot(self.product,{},slot,1,
                image_transport=lambda _request:{'data':[{'b64_json':base64.b64encode(image).decode()}]},
                transport=lambda *_a:self.response({'checks':checks,'reasons':['identity uncertain']}))
        self.assertEqual('failed',result['status'])
        self.assertFalse((self.product/'output/image-hard-gate.json').exists())

    def test_successful_actual_image_adapter_passes_existing_receipt_contract(self):
        from scripts.model_production import run_model_stage,run_model_image_slot
        buffer=io.BytesIO();Image.new('RGB',(960,1280),'blue').save(buffer,format='PNG');image=buffer.getvalue()
        for sku in self.skus:(self.root/sku['local_image_path']).write_bytes(image)
        write_source_manifest(self.product)
        run_model_stage(self.product,'ecommerce_design',{},transport=lambda *_a:self.response())
        plan=json.loads((self.product/'output/image-plan.json').read_text(encoding='utf-8'));slot=plan['main_images'][0]
        checks={key:True for key in ('identity','color','sku','structure','markings','legibility','product_visually_dominant')}
        result=run_model_image_slot(self.product,{},slot,1,
            image_transport=lambda _request:{'data':[{'b64_json':base64.b64encode(image).decode()}]},
            transport=lambda *_a:self.response({'checks':checks,'reasons':[]}))
        self.assertEqual('passed',result['status'])
        self.assertEqual('PASS',result['hard_gate_entry']['status'])

    def test_wave_preserves_paid_image_when_vision_result_is_unknown(self):
        from scripts import run_batch
        from scripts.image_wave_executor import execute_image_slot_waves
        from backend.integrations.ai.openai_content import ProviderResultUnknown
        slot={'slot':'main-001','output_path':'output/generated-images/test.png'}
        schedule={'waves':[[slot]],'pending_slot_count':1}
        before=[]
        with patch('scripts.image_wave_executor.pending_slots',return_value=schedule), \
             patch('scripts.model_production.run_model_image_slot',side_effect=ProviderResultUnknown('vision response unknown')) as provider:
            result=execute_image_slot_waves(self.product,1,
                lambda item,attempt:run_batch.run_single_image_slot(self.product,{'model_runtime':'backend_api'},item,attempt),
                before_attempt=lambda item,attempt:before.append(attempt))
        self.assertEqual(1,provider.call_count)
        self.assertEqual([1],before)
        self.assertEqual('result_unknown',result['failed'][0]['status'])

    def test_rebuilt_prerequisite_timestamps_resume_without_payment_but_facts_invalidate(self):
        from scripts.model_production import run_model_stage
        path=self.product/'output/attribute-fill-input.json'
        value=json.loads(path.read_text(encoding='utf-8'));value['generated_at']='old';path.write_text(json.dumps(value),encoding='utf-8')
        first=run_model_stage(self.product,'ecommerce_design',{},transport=lambda *_a:self.response())
        value['generated_at']='rebuilt';path.write_text(json.dumps(value),encoding='utf-8')
        again=run_model_stage(self.product,'ecommerce_design',{},transport=lambda *_a:self.fail('paid again'))
        self.assertEqual(first,again)
        value['category_id']=999;path.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaises(RuntimeError):run_model_stage(self.product,'ecommerce_design',{},transport=lambda *_a:self.fail('changed facts must stop'))


if __name__=='__main__':unittest.main()
