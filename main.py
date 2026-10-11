"""
【已过期】Music Score Editor - Entry Point

旧版编辑器入口，已被 score_studio.py（StudioWindow）取代；后续版本删除。
"""
import warnings

warnings.warn('main.py 已过期，请运行 score_studio.py', DeprecationWarning, stacklevel=2)

from music_score.main_window import main  # noqa: E402

if __name__ == "__main__":
    main()
