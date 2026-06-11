"""pytest 共享工具.

项目里有两个同名 pose_engine.py:
  - ai_vision/pose_engine.py  (旧原型: PoseEngine 工厂 + MediaPipe/YOLO 双后端, 单元测试的对象)
  - ml_pose/pose_engine.py    (现役: MediaPipe Tasks + 分类器, 后端运行时使用)

后端 main_v2_routes 导入时会把 ml_pose 版缓存进 sys.modules['pose_engine'],
之后任何裸 `from pose_engine import ...` 都会命中错误的缓存.
所以测试一律通过本 helper 按文件路径显式加载 ai_vision 版本.
"""
import importlib.util
import os
import sys

_AI_VISION_DIR = os.path.join(os.path.dirname(__file__), '..', 'ai_vision')


def load_ai_vision_pose_engine():
    """按路径加载 ai_vision/pose_engine.py, 与 sys.modules['pose_engine'] 隔离."""
    if 'ai_vision_pose_engine' in sys.modules:
        return sys.modules['ai_vision_pose_engine']
    if _AI_VISION_DIR not in [os.path.abspath(p) for p in sys.path]:
        sys.path.insert(0, _AI_VISION_DIR)  # 供其内部依赖 (form_analyzer 等)
    spec = importlib.util.spec_from_file_location(
        'ai_vision_pose_engine', os.path.join(_AI_VISION_DIR, 'pose_engine.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['ai_vision_pose_engine'] = mod
    spec.loader.exec_module(mod)
    return mod
