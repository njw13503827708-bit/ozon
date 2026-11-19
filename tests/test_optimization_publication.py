import copy
import json
import unittest
import hashlib
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend.db.erp import database
from backend.repositories.local_operations import LocalOperationsRepository
from tests import test_product_optimization as fixtures


class OptimizationPublicationTest(unittest.TestCase):
    def setUp(self):
        fixtures.ProductOptimizationTest.setUp(self)
        self.erp.persist_listing_attributes('a',[{'offer_id':'item-a','description_category_id':123,'type_id':456,
            'height':100,'width':120,'depth':140,'dimension_unit':'mm','weight':200,'weight_unit':'g','barcode':'real-barcode','attributes':[]}])
        with database(self.root) as db:
            db.execute('UPDATE marketplace_listings SET ozon_product_id=?,remote_price=?,currency=? WHERE id=?',('111',50,'CNY',self.listing))
        self.repo = LocalOperationsRepository(self.root); self.repo.initialize()
        self.source = self.service.source('a',self.listing)
        content = self.service.validate_content(self.source,{'title_ru':'Органайзер для ванной','description_ru':'Цвет: белый.',
            'tags':[],'rich_content':{'modules':[{'title':'Органайзер','body':'Цвет: белый.','image_path':''}]}},{})
        self.repo.save_version('a',self.listing,content,self.source['source_revision'],0,'human')
        self.repo.review_version('a',self.listing,1,self.source['source_revision'],'human',{'facts_confirmed':True,'images_authorized':True})
        self.target = {'store_id':'a','listing_id':self.listing,'version':1}
        from backend.services import optimization_publication
        self.publication = optimization_publication

    def test_update_locks_existing_target_and_preserves_price(self):
        review = self.publication.build_optimization_review(self.root,[self.target])
        self.assertEqual('UPDATE',review['items'][0]['operation'])
        item = review['items'][0]['payload']
        self.assertEqual('item-a',item['offer_id'])
        self.assertEqual('50',item['price'])
        self.assertFalse({'stocks','warehouse_id','new_description_category_id'} & set(item))
        with self.assertRaises(PermissionError):
            self.publication.build_optimization_review(self.root,[{**self.target,'store_id':'b'}])

    def test_new_edit_invalidates_review_and_unreviewed_blocks_batch(self):
        review = self.publication.build_optimization_review(self.root,[self.target])
        old = self.repo.version('a',self.listing,1)['content']
        self.repo.save_version('a',self.listing,{**old,'title_ru':'Изменённый текст'},self.source['source_revision'],1,'human')
        with self.assertRaises(RuntimeError):
            self.publication.authorize_optimization_update(self.root,review['review_hash'],'human')
        with self.assertRaises(RuntimeError):
            self.publication.build_optimization_review(self.root,[{**self.target,'version':2}])

    def test_confirm_duplicate_sends_once_and_task_id_is_not_success(self):
        review = self.publication.build_optimization_review(self.root,[self.target])
        authorization = self.publication.authorize_optimization_update(self.root,review['review_hash'],'human')
        calls = []
        class Client:
            def get_products_info(_self, offers):
                return {'items':[{'offer_id':'item-a','id':111,'sku':12345,'price':'50','currency_code':'CNY'}]}
            def get_product_attributes(_self, offers):
                return {'result':[{'offer_id':'item-a',**self.source['original_transport'],'attributes':self.source['attributes']}]}
            def import_products(_self, items):
                calls.append(items);return {'result':{'task_id':99}}
        with patch.object(self.publication,'client_for',return_value=Client()):
            result = self.publication.execute_optimization_update(self.root,authorization['authorization_id'])
            again = self.publication.execute_optimization_update(self.root,authorization['authorization_id'])
        self.assertEqual(1,len(calls))
        self.assertEqual('REMOTE_PENDING',result['items'][0]['status'])
        self.assertEqual(result,again)

    def test_timeout_is_unknown_and_cannot_resend(self):
        review = self.publication.build_optimization_review(self.root,[self.target])
        auth = self.publication.authorize_optimization_update(self.root,review['review_hash'],'human')
        class Client:
            def get_products_info(_self,offers):return {'items':[{'offer_id':'item-a','id':111,'sku':12345,'price':'50','currency_code':'CNY'}]}
            def get_product_attributes(_self, offers):
                return {'result':[{'offer_id':'item-a',**self.source['original_transport'],'attributes':self.source['attributes']}]}
            def import_products(_self,items):raise TimeoutError('after send')
        with patch.object(self.publication,'client_for',return_value=Client()) as client:
            self.assertEqual('RESULT_UNKNOWN',self.publication.execute_optimization_update(self.root,auth['authorization_id'])['items'][0]['status'])
            self.publication.execute_optimization_update(self.root,auth['authorization_id'])
            self.assertEqual(1,client.call_count)

    def test_processing_task_is_not_field_success_until_readback_matches(self):
        review = self.publication.build_optimization_review(self.root,[self.target])
        auth = self.publication.authorize_optimization_update(self.root,review['review_hash'],'human')
        class Client:
            def get_products_info(_self,offers):return {'items':[{'offer_id':'item-a','id':111,'sku':12345,'price':'50','currency_code':'CNY','name':'Старый текст'}]}
            def import_products(_self,items):return {'result':{'task_id':99}}
            def get_import_info(_self,task):return {'result':{'items':[{'offer_id':'item-a','status':'imported','product_id':111}]}}
            def get_product_attributes(_self,offers):return {'result':[{'offer_id':'item-a',**self.source['original_transport'],'attributes':[]}]}
        with patch.object(self.publication,'client_for',return_value=Client()):
            self.publication.execute_optimization_update(self.root,auth['authorization_id'])
            result = self.publication.reconcile_optimization_update(self.root,auth['authorization_id'])
        self.assertEqual('REMOTE_PENDING',result['items'][0]['status'])
        self.assertFalse(result['items'][0]['field_results']['title'])

    def test_changed_price_before_dispatch_sends_zero_updates(self):
        review = self.publication.build_optimization_review(self.root,[self.target])
        auth = self.publication.authorize_optimization_update(self.root,review['review_hash'],'human')
        class Client:
            def get_products_info(_self,offers):return {'items':[{'offer_id':'item-a','id':111,'sku':12345,'price':'99','currency_code':'CNY'}]}
            def import_products(_self,items):raise AssertionError('changed price cannot be sent')
        with patch.object(self.publication,'client_for',return_value=Client()):
            with self.assertRaises(RuntimeError): self.publication.execute_optimization_update(self.root,auth['authorization_id'])

    def _matching_readback(self, rows):
        review = self.publication.build_optimization_review(self.root,[self.target])
        auth = self.publication.authorize_optimization_update(self.root,review['review_hash'],'human')
        payload = review['items'][0]['payload']
        writes = []
        class Client:
            def get_products_info(_self, offers):
                return {'items':[{'offer_id':'item-a','id':111,'sku':12345,'price':'50','currency_code':'CNY','name':payload['name']}]}
            def import_products(_self, items):
                writes.append(items); return {'result':{'task_id':99}}
            def get_import_info(_self, task): return {'result':{'items':rows}}
            def get_product_attributes(_self, offers):
                return {'result':[{'offer_id':'item-a',**self.source['original_transport'],'attributes':payload['attributes']}]}
        with patch.object(self.publication,'client_for',return_value=Client()):
            self.publication.execute_optimization_update(self.root,auth['authorization_id'])
            writes.clear()
            result = self.publication.reconcile_optimization_update(self.root,auth['authorization_id'])
        self.assertEqual([], writes)
        self.assertEqual(0, result['write_api_calls'])
        return result['items'][0]

    def test_pending_with_matching_fields_is_not_success(self):
        result = self._matching_readback([{'offer_id':'item-a','product_id':111,'status':'pending'}])
        self.assertEqual('REMOTE_PENDING',result['status'])
        self.assertEqual('pending',result['processing_status'])

    def test_missing_other_offer_and_wrong_identity_never_succeed(self):
        for rows in ([],[{'offer_id':'other','product_id':111,'status':'imported'}],
                     [{'offer_id':'item-a','product_id':999,'status':'imported'}]):
            with self.subTest(rows=rows):
                self.assertNotEqual('SUCCEEDED',self._matching_readback(rows)['status'])

    def test_failed_and_skipped_receipts_are_distinct_from_success(self):
        self.assertEqual('FAILED_REMOTE',self._matching_readback([{'offer_id':'item-a','product_id':111,'status':'failed'}])['status'])
        self.assertEqual('SKIPPED_NO_CHANGE',self._matching_readback([{'offer_id':'item-a','product_id':111,'status':'skipped'}])['status'])

    def test_imported_exact_target_and_matching_fields_succeeds(self):
        self.assertEqual('SUCCEEDED',self._matching_readback([{'offer_id':'item-a','product_id':111,'status':'imported'}])['status'])

    def _image_version(self, *, qc='ACCEPTED_IDENTITY', hosted=True, store='a'):
        job = 'a'*32; name = 'b'*32+'.png'
        path = self.root/'runtime/optimization-tasks'/job/'assets/generated'/name
        path.parent.mkdir(parents=True); path.write_bytes(b'registered-test-image')
        asset = {'reference':f'asset://{job}/{name}', 'job_id':job,'name':name,
                 'organization_id':self.source['organization_id'],'store_id':store,'listing_id':self.listing,
                 'source_revision':self.source['source_revision'],'path':path.relative_to(self.root).as_posix(),
                 'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'qc_state':qc,'public_url':None}
        if hosted:
            asset.update(public_url='https://fixture.trycloudflare.com/'+name,
                         hosting={'sha256':asset['sha256'],'expires_at':(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat()})
        (path.parents[2]/'assets.json').write_text(json.dumps([asset]))
        old = self.repo.version('a',self.listing,1)['content']
        old.update(image_selection=[asset['reference'],*self.source['images']],generated_images=[asset])
        self.repo.save_version('a',self.listing,old,self.source['source_revision'],1,'human')
        self.repo.review_version('a',self.listing,2,self.source['source_revision'],'human',{})
        self.target['version']=2
        return asset,path

    def test_generated_selection_is_sent_in_order_and_frozen(self):
        asset,_ = self._image_version()
        review = self.publication.build_optimization_review(self.root,[self.target])
        item = review['items'][0]
        self.assertEqual([asset['public_url'],*self.source['images']],item['payload']['images'])
        self.assertEqual(asset['sha256'],item['image_evidence'][0]['sha256'])

    def test_old_primary_image_cannot_override_selected_first_image(self):
        source=copy.deepcopy(self.source);source['original_transport']['primary_image']='https://ir.ozone.ru/s3/old-primary.jpg'
        payload,*_=self.publication._compile(self.root,source,self.repo.version('a',self.listing,1))
        self.assertNotIn('primary_image',payload)

    def test_title_only_does_not_compile_or_replace_opaque_rich_and_tags(self):
        source=copy.deepcopy(self.source)
        source['attributes']=[{'id':23171,'values':[{'value':'#ванная'}]}, {'id':11254,'values':[{'value':'{"original":"opaque"}'}]}]
        version=self.repo.version('a',self.listing,1)
        version['content'].update(changed_fields=['title_ru'],tags=['#ванная'],rich_content={'modules':[]})
        payload,*_=self.publication._compile(self.root,source,version)
        self.assertEqual(source['attributes'],payload['attributes'])

    def test_unreviewed_unhosted_foreign_or_tampered_images_block_review(self):
        for options in ({'qc':'NEEDS_IDENTITY_REVIEW'},{'hosted':False},{'store':'b'}):
            with self.subTest(options=options):
                self.setUp();self._image_version(**options)
                with self.assertRaises(ValueError):self.publication.build_optimization_review(self.root,[self.target])
        self.setUp();_,path=self._image_version();path.write_bytes(b'tamper')
        with self.assertRaises(ValueError):self.publication.build_optimization_review(self.root,[self.target])

    def test_generated_image_readback_mismatch_cannot_succeed(self):
        asset,path = self._image_version()
        with patch('backend.services.listing_image_tools.public_image_bytes',return_value=path.read_bytes()):
            result = self._matching_readback([{'offer_id':'item-a','product_id':111,'status':'imported'}])
        self.assertNotEqual('SUCCEEDED',result['status'])
        self.assertFalse(result['image_results']['verified'])

    def test_changed_original_image_selection_requires_readback(self):
        with database(self.root) as db:
            db.execute('UPDATE marketplace_listings SET remote_images_json=? WHERE id=?',
                       (json.dumps([*self.source['images'],'https://ir.ozone.ru/s3/second.jpg']),self.listing))
        self.source=self.service.source('a',self.listing)
        content=self.repo.version('a',self.listing,1)['content']
        content['image_selection']=[self.source['images'][0]]
        self.repo.save_version('a',self.listing,content,self.source['source_revision'],1,'human')
        self.repo.review_version('a',self.listing,2,self.source['source_revision'],'human',{})
        self.target['version']=2
        result=self._matching_readback([{'offer_id':'item-a','product_id':111,'status':'imported'}])
        self.assertNotEqual('SUCCEEDED',result['status'])

    def test_one_stale_target_blocks_entire_batch_before_any_client(self):
        target={**self.target,'listing_id':self.listing+100}
        with patch.object(self.publication,'client_for') as client:
            with self.assertRaises(KeyError):self.publication.build_optimization_review(self.root,[self.target,target])
            client.assert_not_called()

    def test_changed_image_hosting_invalidates_final_confirmation(self):
        asset,path=self._image_version()
        review=self.publication.build_optimization_review(self.root,[self.target])
        registry=path.parents[2]/'assets.json'
        rows=json.loads(registry.read_text(encoding='utf-8'));rows[0]['public_url']='https://changed.trycloudflare.com/new.png'
        registry.write_text(json.dumps(rows))
        with self.assertRaises(RuntimeError):self.publication.authorize_optimization_update(self.root,review['review_hash'],'human')

    def test_changed_platform_dimensions_before_dispatch_sends_zero(self):
        review=self.publication.build_optimization_review(self.root,[self.target])
        auth=self.publication.authorize_optimization_update(self.root,review['review_hash'],'human')
        class Client:
            def get_products_info(_self,offers):return {'items':[{'offer_id':'item-a','id':111,'sku':12345,'price':'50','currency_code':'CNY'}]}
            def get_product_attributes(_self,offers):return {'result':[{'offer_id':'item-a',**self.source['original_transport'],'height':999,'attributes':[]}]}
            def import_products(_self,items):raise AssertionError('stale package dimensions must not be overwritten')
        with patch.object(self.publication,'client_for',return_value=Client()):
            with self.assertRaises(RuntimeError):self.publication.execute_optimization_update(self.root,auth['authorization_id'])

    def test_acknowledged_crash_recovers_task_and_queries_without_replay(self):
        review=self.publication.build_optimization_review(self.root,[self.target]);payload=review['items'][0]['payload']
        auth=self.publication.authorize_optimization_update(self.root,review['review_hash'],'human')
        writes=[];queries=[]
        class Client:
            def get_products_info(_self,offers):return {'items':[{'offer_id':'item-a','id':111,'sku':12345,'price':'50','currency_code':'CNY','name':payload['name']}]}
            def get_product_attributes(_self,offers):return {'result':[{'offer_id':'item-a',**self.source['original_transport'],'attributes':payload['attributes']}]}
            def import_products(_self,items):writes.append(items);return {'result':{'task_id':99}}
            def get_import_info(_self,task):queries.append(task);return {'result':{'items':[{'offer_id':'item-a','product_id':111,'status':'imported'}]}}
        submit=self.publication._submit_import_once
        def crash(*args,**kwargs):submit(*args,**kwargs);raise SystemExit('crash after acknowledged intent')
        with patch.object(self.publication,'client_for',return_value=Client()):
            with patch.object(self.publication,'_submit_import_once',side_effect=crash):
                with self.assertRaises(SystemExit):self.publication.execute_optimization_update(self.root,auth['authorization_id'])
            self.publication.execute_optimization_update(self.root,auth['authorization_id'])
            result=self.publication.reconcile_optimization_update(self.root,auth['authorization_id'])
        self.assertEqual(1,len(writes));self.assertEqual([99],queries)
        self.assertEqual('SUCCEEDED',result['items'][0]['status'])

    def test_runtime_readonly_blocks_common_dispatch_even_if_upload_mode_is_forced(self):
        work=self.root/'runtime/write-test';(work/'output').mkdir(parents=True)
        client=unittest.mock.Mock()
        with patch.dict(os.environ,{'ERP_RUNTIME_MODE':'READ_ONLY','UPLOAD_MODE':'production'}):
            with self.assertRaises(RuntimeError):self.publication._submit_import_once(work,client,[{'offer_id':'item-a'}],'update',confirmed_backend=True)
        client.import_products.assert_not_called()


if __name__ == '__main__': unittest.main()
