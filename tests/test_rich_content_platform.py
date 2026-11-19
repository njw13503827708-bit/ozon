import json
import sys
import unittest
from pathlib import Path
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ozon-field-completion'))


class RichContentPlatformTest(unittest.TestCase):
    def test_text_and_owned_image_modules_match_current_official_contract(self):
        from ozon_field_completion.service import compile_rich_content_modules
        modules = [{'title':'Органайзер','body':'Цвет: белый.','image_path':'https://ir.ozone.ru/a.jpg'},
                   {'title':'Описание','body':'Органайзер для ванной.','image_path':''}]
        payload = compile_rich_content_modules(modules)
        schema = json.loads((ROOT/'templates/ozon-rich-content-official.schema.json').read_text(encoding='utf-8'))
        self.assertEqual([], list(Draft7Validator(schema).iter_errors(payload)))
        self.assertEqual(0.3,payload['version'])
        self.assertEqual('raShowcase',payload['content'][0]['widgetName'])
        self.assertEqual('raTextBlock',payload['content'][1]['widgetName'])

    def test_local_private_and_unresolved_images_cannot_be_platform_content(self):
        from ozon_field_completion.service import compile_rich_content_modules
        for value in ('http://127.0.0.1/a.jpg','https://192.168.1.1/a.jpg','file:///a.png','asset://unpublished'):
            with self.assertRaises(ValueError):
                compile_rich_content_modules([{'title':'Описание','body':'Товар','image_path':value}])


if __name__ == '__main__': unittest.main()
