import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


@unittest.skipUnless(sys.platform == 'darwin', 'AppKit is only available on macOS')
class MacDesktopTests(unittest.TestCase):
    def test_dragged_caption_keeps_its_top_edge_when_lyrics_wrap(self):
        from AppKit import NSMakeRect
        from demo.desktop_overlay_macos import caption_origin

        pet = NSMakeRect(900, 40, 110, 120)
        screen = NSMakeRect(0, 0, 1280, 900)
        self.assertEqual(caption_origin(pet, screen, 410, 100), (600, 164))
        self.assertEqual(caption_origin(pet, screen, 410, 100, (200, 680)), (200, 580))
        self.assertEqual(caption_origin(pet, screen, 410, 150, (200, 680)), (200, 530))
        self.assertEqual(caption_origin(pet, screen, 410, 150, (2200, 1500)), (862, 746))

    def test_click_opens_chat_and_music_menu_opens_player(self):
        from demo.desktop_macos import DesktopPet, PetView

        room = Mock()
        desktop = SimpleNamespace(room=room, cancel_knock=Mock(), play_animation=Mock())
        DesktopPet.finish_click(desktop)
        room.open.assert_called_once_with('chat')

        view = PetView.alloc().init()
        view.pet = desktop
        view.openMusic_(None)
        room.open.assert_called_with('music')


class ConfigureGlmTests(unittest.TestCase):
    def test_validated_key_is_saved_and_activates_existing_client(self):
        from demo.glm import Glm
        from demo.main import App

        client = Glm('')
        app = SimpleNamespace(glm=client, pet=SimpleNamespace(speech=None),
                              memory=Mock(), thinker=Mock())
        with patch.object(Glm, 'complete', return_value={'content': 'OK'}), patch('demo.main.save_key') as save:
            App.configure_glm(app, 'test-token')
            save.assert_called_once_with('test-token')
        self.assertTrue(client.ready)
        self.assertIs(app.pet.speech.glm, client)
        app.thinker.start.assert_called_once()

    def test_failed_validation_keeps_existing_configuration(self):
        from demo.glm import Glm, GlmError
        from demo.main import App

        app = SimpleNamespace(glm=Glm('existing-token'))
        with patch.object(Glm, 'complete', side_effect=GlmError('denied')), patch('demo.main.save_key') as save:
            with self.assertRaisesRegex(ValueError, 'GLM 验证失败'):
                App.configure_glm(app, 'rejected-token')
            save.assert_not_called()
        self.assertEqual(app.glm.key, 'existing-token')
