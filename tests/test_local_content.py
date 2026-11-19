import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'collector/local-ingest'))
from local_content import keyword_suggestions, validate_rich_content


class LocalContentTests(unittest.TestCase):
    def test_traceable_deduplicated_product_derived_tags_and_unknown_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            product = Path(temp) / 'P990001'
            (product / 'output').mkdir(parents=True)
            (product / 'output/copy-ru.json').write_text(json.dumps({
                'title_ru': 'Органайзер для кухни',
                'keywords_ru': ['Органайзер для кухни', 'органайзер для кухни', 'смартфон', 'Brand123'],
            }), encoding='utf-8')
            before = {p: p.read_bytes() for p in product.rglob('*') if p.is_file()}
            result = keyword_suggestions(product)
            self.assertEqual([x['tag'] for x in result['items']], ['#органайзеркухни'])
            item = result['items'][0]
            self.assertEqual(item['basis'], '商品资料推导')
            self.assertIn('copy-ru.json', item['source_ref'])
            self.assertEqual(item['original'], 'Органайзер для кухни')
            self.assertEqual(item['metrics'], {'search_volume': None, 'sales': None, 'rank': None})
            self.assertTrue(any('重复' in x for x in result['warnings']))
            self.assertTrue(any('无关' in x for x in result['warnings']))
            self.assertEqual(before, {p: p.read_bytes() for p in product.rglob('*') if p.is_file()})

    def test_missing_russian_material_returns_no_fake_suggestions(self):
        with tempfile.TemporaryDirectory() as temp:
            result = keyword_suggestions(Path(temp))
            self.assertEqual(result['items'], [])
            self.assertIn('俄文', result['missing'])

    def test_rich_content_only_accepts_existing_owned_images(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            product = root / 'products/P990001'
            image = product / 'input/main-images/one.png'
            image.parent.mkdir(parents=True)
            image.write_bytes(b'fixture')
            payload = {'modules': [{'title': 'Товар', 'body': 'Описание',
                                    'image_path': 'products/P990001/input/main-images/one.png'}]}
            self.assertEqual(validate_rich_content(product, root, payload), payload)
            for bad in ['https://example.com/a.png', '../secret.png', 'products/P990002/input/a.png']:
                with self.assertRaises(ValueError):
                    validate_rich_content(product, root, {'modules': [{'title': '', 'body': '', 'image_path': bad}]})

    def test_rich_content_empty_clear_and_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            product = Path(temp) / 'products/P990001'
            self.assertEqual(validate_rich_content(product, Path(temp), {'modules': []}), {'modules': []})
            with self.assertRaises(ValueError):
                validate_rich_content(product, Path(temp), {'modules': [{'title': 'a' * 501, 'body': '', 'image_path': ''}]})
