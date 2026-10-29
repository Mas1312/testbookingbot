"""Страница услуги: галерея фото, длинное описание, ссылка на конкретную услугу."""
import asyncio
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import bot as bot_module  # noqa: E402
import database  # noqa: E402
import server  # noqa: E402
from aiogram.filters import CommandObject  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from test_cabinet import sign_init_data  # noqa: E402

TOKEN = "123456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM"
OTHER_TOKEN = "223456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lN"
OWNER, OTHER_OWNER = 4242, 5252
WEBAPP = os.path.join(os.path.dirname(__file__), "..", "webapp")


def run(coro):
    return asyncio.run(coro)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK)
        database.DB_PATH = os.path.join(self.tmp.name, "t.db")
        server.DEV_SKIP_INITDATA_CHECK = False
        database.init_db()
        database.create_business(1, "Заполнитель", "923456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM")  # id 1 = платформенный
        self.biz = database.create_business(OWNER, "Салон", TOKEN)
        self.other = database.create_business(OTHER_OWNER, "Чужой", OTHER_TOKEN)
        self.bid = self.biz["id"]

    def tearDown(self):
        database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK = self._orig
        self.tmp.cleanup()

    def media(self, business=None):
        return database.create_media((business or self.biz)["id"], "image/jpeg", b"\xff\xd8\xff" + os.urandom(16))

    def media_exists(self, media_id):
        return database.get_media(media_id) is not None

    def payload(self, cls=server.ServiceRequest, **kw):
        data = dict(business_id=self.bid, name="Окрашивание", price=3000, duration_min=120, type="slot",
                    init_data=sign_init_data(OWNER, TOKEN))
        data.update(kw)
        return cls(**data)

    def create(self, **kw):
        return server.admin_create_service(self.payload(**kw))["id"]

    def update(self, service_id, **kw):
        return server.admin_update_service(service_id, self.payload(**kw))

    def listed(self):
        return {s["id"]: s for s in server.admin_list_services(self.bid, init_data=sign_init_data(OWNER, TOKEN))}


class GalleryTest(Base):
    def test_gallery_keeps_order_and_first_photo_is_the_cover(self):
        a, b, c = self.media(), self.media(), self.media()
        sid = self.create(image_ids=[a, b, c])
        service = self.listed()[sid]
        self.assertEqual([i["id"] for i in service["images"]], [a, b, c])
        self.assertEqual(service["image_id"], a)
        self.assertEqual(service["image_url"], f"/api/media/{a}")
        self.assertEqual(service["images"][1]["url"], f"/api/media/{b}")

    def test_reordering_and_removing(self):
        a, b, c = self.media(), self.media(), self.media()
        sid = self.create(image_ids=[a, b, c])
        self.update(sid, image_ids=[c, a])
        service = self.listed()[sid]
        self.assertEqual([i["id"] for i in service["images"]], [c, a])
        self.assertEqual(service["image_id"], c)
        self.assertFalse(self.media_exists(b))                       # убранное фото удалено
        self.assertTrue(self.media_exists(a) and self.media_exists(c))

    def test_removing_every_photo(self):
        a = self.media()
        sid = self.create(image_ids=[a])
        self.update(sid, image_ids=[])
        service = self.listed()[sid]
        self.assertEqual((service["images"], service["image_id"], service["image_url"]), ([], None, None))
        self.assertFalse(self.media_exists(a))

    def test_old_clients_sending_only_image_id_still_work(self):
        a, b = self.media(), self.media()
        sid = self.create(image_id=a)
        self.assertEqual([i["id"] for i in self.listed()[sid]["images"]], [a])
        self.update(sid, image_id=b)
        service = self.listed()[sid]
        self.assertEqual([i["id"] for i in service["images"]], [b])
        self.assertFalse(self.media_exists(a))
        self.update(sid, image_id=None)
        self.assertEqual(self.listed()[sid]["images"], [])
        self.assertFalse(self.media_exists(b))

    def test_duplicates_are_collapsed_and_limit_is_enforced(self):
        a = self.media()
        sid = self.create(image_ids=[a, a, a])
        self.assertEqual(len(self.listed()[sid]["images"]), 1)
        too_many = [self.media() for _ in range(6)]
        with self.assertRaises(ValidationError):
            self.payload(image_ids=too_many)
        five = too_many[:5]
        self.update(sid, image_ids=five)
        self.assertEqual(len(self.listed()[sid]["images"]), 5)

    def test_foreign_media_is_rejected(self):
        mine, foreign = self.media(), self.media(self.other)
        for call in (lambda: self.create(image_ids=[mine, foreign]), lambda: self.create(image_id=foreign)):
            with self.assertRaises(HTTPException) as ctx:
                call()
            self.assertEqual(ctx.exception.status_code, 400)
        sid = self.create(image_ids=[mine])
        with self.assertRaises(HTTPException) as ctx:
            self.update(sid, image_ids=[foreign])
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual([i["id"] for i in self.listed()[sid]["images"]], [mine])
        self.assertTrue(self.media_exists(foreign))                  # чужое фото не тронуто

    def test_deleting_the_service_removes_its_photos(self):
        a, b = self.media(), self.media()
        sid = self.create(image_ids=[a, b])
        server.admin_delete_service(sid, self.bid, init_data=sign_init_data(OWNER, TOKEN))
        self.assertFalse(self.media_exists(a) or self.media_exists(b))
        self.assertEqual(database.get_service_gallery(self.bid), {})

    def test_photo_used_elsewhere_is_not_deleted(self):
        a = self.media()
        s1 = self.create(image_ids=[a])
        s2 = self.create(name="Другая", image_ids=[a])
        server.admin_delete_service(s1, self.bid, init_data=sign_init_data(OWNER, TOKEN))
        self.assertTrue(self.media_exists(a))
        self.assertEqual([i["id"] for i in self.listed()[s2]["images"]], [a])

    def test_orphan_cleanup_keeps_gallery_photos(self):
        attached, loose = self.media(), self.media()
        self.create(image_ids=[attached])
        conn = database.get_connection()
        conn.execute("UPDATE media SET created_at = datetime('now', '-3 days')")
        conn.commit()
        conn.close()
        database.delete_orphan_media()
        self.assertTrue(self.media_exists(attached))
        self.assertFalse(self.media_exists(loose))

    def test_public_list_shows_gallery_and_hides_inactive_services(self):
        a, b = self.media(), self.media()
        shown = self.create(image_ids=[a, b])
        hidden = self.create(name="Скрытая", image_ids=[self.media()])
        self.update(hidden, is_active=False)
        public = {s["id"]: s for s in server.api_services(self.bid)}
        self.assertEqual(set(public), {shown})
        self.assertEqual([i["id"] for i in public[shown]["images"]], [a, b])

    def test_gallery_of_one_business_never_leaks_into_another(self):
        self.create(image_ids=[self.media()])
        other_service = database.create_service(self.other["id"], "Чужая", 100, 30, "slot")
        public = server.api_services(self.other["id"])
        self.assertEqual([(s["id"], s["images"]) for s in public], [(other_service, [])])


class MigrationTest(Base):
    def test_existing_single_photo_becomes_the_first_gallery_photo(self):
        a = self.media()
        sid = database.create_service(self.bid, "Старая", 100, 30, "slot")
        conn = database.get_connection()
        conn.execute("UPDATE services SET image_id = ? WHERE id = ?", (a, sid))   # как было до галереи
        conn.execute("DELETE FROM service_images")
        conn.commit()
        conn.close()
        database.init_db()
        self.assertEqual(database.get_service_gallery(self.bid), {sid: [a]})
        database.init_db()                                                       # повторный запуск ничего не дублирует
        self.assertEqual(database.get_service_gallery(self.bid), {sid: [a]})


class DescriptionTest(Base):
    def test_long_description_with_paragraphs_is_stored_as_is(self):
        text = "Первый абзац.\n\nВторой абзац.\n" + "я" * 1900
        sid = self.create(description=text)
        self.assertEqual(self.listed()[sid]["description"], text.strip())

    def test_limit(self):
        self.create(description="я" * 2000)
        with self.assertRaises(ValidationError):
            self.payload(description="я" * 2001)


class FakeMessage:
    def __init__(self):
        self.from_user = SimpleNamespace(id=777)
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class StartLinkTest(Base):
    def start(self, args, business=None):
        message = FakeMessage()
        run(bot_module.cmd_start(message, (business or self.biz)["id"], CommandObject(command="start", args=args)))
        text, kwargs = message.answers[0]
        button = kwargs["reply_markup"].inline_keyboard[0][0]
        return text, button.web_app.url

    def test_service_link_opens_that_service(self):
        sid = self.create()
        text, url = self.start(f"svc_{sid}")
        self.assertTrue(url.endswith(f"/?business_id={self.bid}&service={sid}"))
        self.assertIn("Окрашивание", text)

    def test_ordinary_start_is_unchanged(self):
        text, url = self.start(None)
        self.assertTrue(url.endswith(f"/?business_id={self.bid}"))
        self.assertIn("Привет! Это бот записи", text)

    def test_bad_links_fall_back_to_the_plain_start(self):
        hidden = self.create(name="Скрытая")
        self.update(hidden, name="Скрытая", is_active=False)
        foreign = database.create_service(self.other["id"], "Чужая", 100, 30, "slot")
        for args in ("svc_999999", "svc_abc", "svc_", f"svc_{hidden}", f"svc_{foreign}", "что-то", "svc_1; drop"):
            with self.subTest(args=args):
                _, url = self.start(args)
                self.assertTrue(url.endswith(f"/?business_id={self.bid}"), url)


class UiTest(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(WEBAPP, name), encoding="utf-8") as f:
            return f.read()

    def test_wiring(self):
        html, js, css = self.read("index.html"), self.read("app.js"), self.read("style.css")
        for ident in ("screen-service", "service-gallery-view", "service-page-title", "service-page-desc",
                      "service-page-book", "service-gallery", "service-photo-btn", "service-photo-file",
                      "service-share-btn"):
            self.assertIn(f'id="{ident}"', html)
            self.assertIn(f'"{ident}"', js)
        self.assertIn("image_ids", js)
        self.assertIn("svc_", js)
        self.assertIn("pre-wrap", css)                    # абзацы описания сохраняются
        self.assertNotIn("${service.description}", js)    # описание вставляется только через textContent / escapeHtml


if __name__ == "__main__":
    unittest.main()
