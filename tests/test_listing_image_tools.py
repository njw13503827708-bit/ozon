import base64
import copy
import io
import os
import unittest
from unittest.mock import patch
from PIL import Image

from tests import test_product_optimization as fixtures
from backend.repositories.local_operations import LocalOperationsRepository
from backend.services.optimization_agent import OptimizationAgent


class ListingImageToolsTest(unittest.TestCase):
    def setUp(self):
        fixtures.ProductOptimizationTest.setUp(self)
        LocalOperationsRepository(self.root).initialize()
        env = patch.dict(os.environ, {'OPENAI_API_KEY':'test-image-key','OPENAI_TEXT_MODEL':'configured-model'})
        env.start(); self.addCleanup(env.stop)
        self.source = self.service.source('a',self.listing)
        agent = OptimizationAgent(self.root)
        self.job = agent.start('a',self.listing,0,self.source['source_revision'],['images'],
                               {'date_from':'2026-09-26','date_to':'2026-10-02'},'host')
        self.context = self.job['context']
        output = io.BytesIO(); Image.new('RGB',(960,1280),'blue').save(output,format='PNG')
        self.image = output.getvalue()
        self.plan = [{'prompt':'Preserve actual product and markings.','reference_indexes':[0]}]

    def test_cross_listing_image_refused_before_download_or_generation(self):
        from backend.services.listing_image_tools import prepare_listing_images
        foreign = copy.deepcopy(self.context); foreign['source']['listing_id'] += 1
        with patch('backend.services.listing_image_tools.download_reference') as download:
            with self.assertRaises(PermissionError): prepare_listing_images(self.root,self.job['id'],foreign,self.plan)
            download.assert_not_called()

    def test_original_never_overwritten_and_registered_asset_reloads_without_recharge(self):
        from backend.services.listing_image_tools import prepare_listing_images, asset_for
        def provider(request):
            return {'data':[{'b64_json':base64.b64encode(self.image).decode()}], 'usage':{'total_tokens':1}}
        with patch('backend.services.listing_image_tools.download_reference',return_value=self.image), patch('scripts.openai_images2.urllib.request.OpenerDirector.open') as network:
            assets = prepare_listing_images(self.root,self.job['id'],self.context,self.plan,transport=provider)
            again = prepare_listing_images(self.root,self.job['id'],self.context,self.plan,transport=lambda _r:self.fail('double charge'))
            self.assertEqual(assets,again)
            network.assert_not_called()
        self.assertEqual('NEEDS_IDENTITY_REVIEW',assets[0]['qc_state'])
        self.assertEqual(assets[0],asset_for(self.root,self.source,assets[0]['reference']))
        self.assertEqual(self.source['source_revision'],self.service.source('a',self.listing)['source_revision'])
        asset_path = self.root/assets[0]['path']; asset_path.write_bytes(b'tampered')
        with self.assertRaises(ValueError): asset_for(self.root,self.source,assets[0]['reference'])

    def test_unregistered_asset_refused(self):
        from backend.services.listing_image_tools import asset_for
        with self.assertRaises(ValueError): asset_for(self.root,self.source,'asset://'+self.job['id']+'/fake.png')

    def test_review_and_hosting_are_explicit_not_model_qc(self):
        from backend.services.listing_image_tools import prepare_listing_images, review_asset, publish_asset
        with patch('backend.services.listing_image_tools.download_reference',return_value=self.image):
            assets = prepare_listing_images(self.root,self.job['id'],self.context,self.plan,
                transport=lambda _r:{'data':[{'b64_json':base64.b64encode(self.image).decode()}]})
        asset = review_asset(self.root,self.source,assets[0]['reference'],'human')
        self.assertEqual('ACCEPTED_IDENTITY',asset['qc_state'])
        self.assertEqual('human',asset['identity_review']['actor'])
        with patch.dict(os.environ,{'CAF_IMAGE_HOSTING_APPROVED':'0'}), patch('ozon_uploader.image_channels.start_image_channel') as host:
            with self.assertRaises(RuntimeError):publish_asset(self.root,self.source,asset['reference'])
            host.assert_not_called()


if __name__ == '__main__': unittest.main()
