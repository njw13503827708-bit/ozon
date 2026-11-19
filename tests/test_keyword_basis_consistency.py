import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from tests.test_workbench import workbench, make_product, FakeRequest, write_json
from local_content import keyword_suggestions


class KeywordBasisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.product = make_product(self.root)
        write_json(self.product / 'output/copy-ru.json', {'title_ru':'Органайзер для кухни',
                   'description_ru':'Органайзер для кухни и хранения посуды.', 'keywords_ru':['хранения посуды']})
        self.patches = [patch.object(workbench, 'ROOT', self.root), patch.object(workbench, 'PRODUCTS_DIR', self.root/'products')]
        for p in self.patches:p.start()
        self.items = keyword_suggestions(self.product)['items']
        self.old, self.new = self.items[0]['tag'], self.items[1]['tag']
        await self.save({'tags':[self.old], 'keyword_selection':[self.old],
                         'keyword_source_revision':self.items[0].get('source_revision', 'baseline')})

    async def asyncTearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    async def save(self, payload):
        if 'source_revision' not in self.items[0]:
            payload = {key:value for key,value in payload.items() if key != 'keyword_source_revision'}
        return await workbench.save_workbench_draft('P000101', FakeRequest(payload))

    def draft(self):return json.loads((self.product/'output/workbench-draft.json').read_text(encoding='utf-8'))

    async def test_delete_and_clear_remove_basis(self):
        await self.save({'tags':['#ручной']})
        self.assertEqual(self.draft()['keyword_basis'], [])
        await self.save({'tags':[]})
        self.assertEqual(self.draft()['keyword_basis'], [])
        self.assertEqual(self.draft()['keyword_selection'], [])

    async def test_new_accept_preserves_old_basis(self):
        await self.save({'tags':[self.old,self.new], 'keyword_selection':[self.new],
                         'keyword_source_revision':self.items[1].get('source_revision','baseline')})
        self.assertEqual({x['tag'] for x in self.draft()['keyword_basis']}, {self.old,self.new})

    async def test_copy_and_rich_only_preserve_basis(self):
        before=self.draft()['keyword_basis']
        await self.save({'title_ru':'Ручное название','description_ru':'Ручное описание'})
        await self.save({'rich_content_draft':{'modules':[]}})
        self.assertEqual(self.draft()['keyword_basis'],before)

    async def test_api_cannot_record_basis_for_absent_tag(self):
        before=self.draft()
        with self.assertRaises(HTTPException):
            await self.save({'tags':[], 'keyword_selection':[self.new],
                             'keyword_source_revision':self.items[1].get('source_revision','baseline')})
        self.assertEqual(self.draft(),before)

    async def test_manual_tag_does_not_gain_basis(self):
        await self.save({'tags':[self.old,self.new]})
        self.assertEqual([x['tag'] for x in self.draft()['keyword_basis']], [self.old])

    async def test_source_change_requires_fresh_confirmation(self):
        before=self.draft()
        write_json(self.product/'output/copy-ru.json',{'title_ru':'Органайзер для кухни',
                   'description_ru':'Новое описание для кухни', 'keywords_ru':['кухни']})
        with self.assertRaises(HTTPException) as error:
            await self.save({'title_ru':'Другая ручная правка'})
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(self.draft(),before)

    async def test_stale_accept_and_forged_basis_are_rejected(self):
        write_json(self.product/'output/copy-ru.json', {'title_ru':'Органайзер для кухни',
                   'description_ru':'Описание изменено', 'keywords_ru':['кухни']})
        with self.assertRaises(HTTPException) as error:
            await self.save({'tags':[self.old], 'keyword_selection':[self.old],
                             'keyword_source_revision':self.items[0]['source_revision']})
        self.assertEqual(error.exception.status_code,409)
        with self.assertRaises(HTTPException) as error:
            await self.save({'keyword_basis':self.items})
        self.assertEqual(error.exception.status_code,422)
        await self.save({'tags':[]})
        self.assertEqual(self.draft()['keyword_basis'],[])

    async def test_explicit_foreign_organization_cannot_read_or_write_product(self):
        token=workbench.CURRENT_OPERATOR.set({'id':'foreign','organization_id':'foreign-org','is_host_device':False})
        before=self.draft()
        try:
            with self.assertRaises(HTTPException) as error:
                await self.save({'title_ru':'Foreign change'})
            self.assertEqual(error.exception.status_code,403)
            with self.assertRaises(HTTPException) as error:
                await workbench.local_keyword_suggestions('P000101')
            self.assertEqual(error.exception.status_code,403)
        finally:workbench.CURRENT_OPERATOR.reset(token)
        self.assertEqual(self.draft(),before)
