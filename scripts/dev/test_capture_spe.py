"""Capture Score Studio for Wiley SPE figure."""
import os
import sys
import time

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
os.environ['COMPOSERCHAT_LANG'] = 'en'
os.environ['QT_ENABLE_HIGHDPI_SCALING'] = '1'

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer
from PIL import Image
import i18n
import score_studio

def capture(scale_factor=1, out_path='tmp/studio_test.png'):
    os.environ['QT_SCALE_FACTOR'] = str(scale_factor)
    i18n.set_language('en', persist=False)
    
    app = QApplication.instance() or QApplication(sys.argv)
    app._retranslator = i18n.Retranslator(app)
    
    # Save original index state
    index_orig = None
    if os.path.exists(score_studio.CHAT_INDEX_PATH):
        with open(score_studio.CHAT_INDEX_PATH, 'r', encoding='utf-8') as f:
            index_orig = f.read()

    new_chat_id = None
    win = None
    try:
        win = score_studio.StudioWindow()
        win.resize(1600, 1000)
        win.show()
        app.processEvents()

        # Create fresh new session
        win._new_chat_session()
        new_chat_id = win._chat_id
        app.processEvents()

        # Re-apply translation
        app._retranslator.apply('en', persist=False)
        app.processEvents()

        # Enter command in chat
        win.chat_input.setPlainText('/compose-grammar chorale G seed=3')
        win.send_chat_message()

        # Wait for completion
        deadline = 60
        while win._chat_busy and deadline > 0:
            app.processEvents()
            time.sleep(0.1)
            deadline -= 0.1

        # Wait a bit for synth & rendering
        for _ in range(20):
            app.processEvents()
            time.sleep(0.05)

        # Re-apply translation once more to catch any dynamically added widgets
        app._retranslator.apply('en', persist=False)
        app.processEvents()

        # Ensure right tab is active (mixer tab index 0)
        win.tabs.setCurrentIndex(0)
        app.processEvents()

        # Grab pixmap
        pix = win.grab()
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        temp_png = out_path + '.raw.png'
        pix.save(temp_png)

        # Use PIL to save with 300 DPI metadata
        im = Image.open(temp_png)
        print(f"Captured: {im.size}, Mode: {im.mode}")
        im.save(out_path, dpi=(300, 300))
        if os.path.exists(temp_png):
            os.remove(temp_png)
    finally:
        # Prevent closeEvent from persisting this temporary chat
        if win is not None:
            win._chat_id = None
            win.close()
            win.deleteLater()

        # Clean up created chat files
        if new_chat_id:
            paths = score_studio.StudioWindow._chat_paths(None, new_chat_id)
            for p in paths.values():
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

        # Restore index
        if index_orig is not None:
            with open(score_studio.CHAT_INDEX_PATH, 'w', encoding='utf-8') as f:
                f.write(index_orig)
    print("Done capture:", out_path)

if __name__ == '__main__':
    scale = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    out = sys.argv[2] if len(sys.argv) > 2 else 'tmp/studio_test.png'
    capture(scale, out)
