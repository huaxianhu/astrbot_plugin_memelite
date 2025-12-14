import asyncio
import io
from dataclasses import dataclass, field
from typing import Literal

try:
    from meme_generator import Meme, get_memes
    from meme_generator.version import __version__
    MEME_GENERATOR_AVAILABLE = True
except ImportError as e:
    MEME_GENERATOR_AVAILABLE = False
    IMPORT_ERROR = str(e)
    # Provide dummy values - set version high to avoid incorrect is_py_version detection
    __version__ = "999.0.0"
    # Type stubs for when meme_generator is not available
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from meme_generator import Meme as MemeType
        Meme = MemeType
    else:
        Meme = type("Meme", (), {})  # Create a dummy class for runtime
    get_memes = lambda: []  # type: ignore

from astrbot import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .param import ParamsCollector


@dataclass
class MemeProperties:
    disabled: bool = False
    labels: list[Literal["new", "hot"]] = field(default_factory=list)


class MemeManager:
    is_py_version = tuple(map(int, __version__.split("."))) < (0, 2, 0)
    def __init__(self, config: AstrBotConfig, collect: ParamsCollector):
        self.conf = config
        self.collect = collect
        self.memes: list[Meme] = []
        self.meme_keywords: list[str] = []
        self._memes_loaded = False

        if not MEME_GENERATOR_AVAILABLE:
            logger.error("=" * 60)
            logger.error("meme-generator 导入失败！")
            logger.error(f"错误信息: {IMPORT_ERROR}")
            logger.error("请尝试以下解决方案:")
            logger.error("1. 确保已安装 meme-generator: pip install meme-generator")
            logger.error("2. 如果已安装，尝试重新安装: pip uninstall meme-generator && pip install meme-generator")
            logger.error("3. 检查是否缺少系统依赖 (Linux): sudo apt-get install -y libegl1 libgles2 libgl1")
            logger.error("4. 查看详细文档: https://github.com/MeetWq/meme-generator")
            logger.error("=" * 60)
            return

        if self.is_py_version:
            from meme_generator.download import check_resources
            from meme_generator.utils import run_sync, render_meme_list
            self.render_meme_list = render_meme_list
            self.check_resources_func = check_resources
            self.run_sync = run_sync
        else:
            from meme_generator.tools import MemeProperties, MemeSortBy, render_meme_list
            from meme_generator.resources import check_resources_in_background
            from meme_generator import Image as MemeImage
            self.render_meme_list = render_meme_list
            self.check_resources_func = check_resources_in_background
            self.MemeImage = MemeImage

    def _load_memes(self):
        """加载meme列表和关键词"""
        if self._memes_loaded:
            return
        if not MEME_GENERATOR_AVAILABLE:
            logger.error("无法加载memes: meme-generator 未正确安装")
            return
        try:
            self.memes = get_memes()
            if not self.memes:
                logger.warning("未找到任何meme，可能是资源未下载")
                logger.info("请等待资源下载完成，或手动下载资源")
                return
            self.meme_keywords = [
                k
                for m in self.memes
                for k in (m.keywords if self.is_py_version else m.info.keywords)
            ]
            self._memes_loaded = True
            logger.info(f"成功加载 {len(self.memes)} 个meme，共 {len(self.meme_keywords)} 个关键词")
        except AttributeError as e:
            logger.error(f"加载meme失败 (属性错误): {e}")
            logger.error("这可能是因为 meme-generator 资源未完全下载")
            logger.info("请等待资源下载完成后重启插件")
            self.memes = []
            self.meme_keywords = []
        except Exception as e:
            logger.error(f"加载meme失败: {e}")
            self.memes = []
            self.meme_keywords = []

    async def check_resources(self):
        if not MEME_GENERATOR_AVAILABLE:
            logger.error("跳过资源检查: meme-generator 未正确安装")
            return
        if not self.conf["is_check_resources"]:
            logger.info("跳过资源检查，直接加载memes...")
            self._load_memes()
            return
        logger.info("开始检查memes资源...")
        try:
            if self.is_py_version:
                await self.check_resources_func()
            else:
                await asyncio.to_thread(self.check_resources_func)
            logger.info("资源检查完成，开始加载memes...")
        except Exception as e:
            logger.warning(f"资源检查失败: {e}，尝试直接加载memes...")
        self._load_memes()

    def find_meme(self, keyword: str) -> Meme | None:
        if not self._memes_loaded:
            logger.warning("Memes尚未加载")
            return None
        for meme in self.memes:
            keywords = meme.keywords if self.is_py_version else meme.info.keywords
            if keyword == meme.key or keyword in keywords:
                return meme

    def is_meme_keyword(self, meme_name: str) -> bool:
        if not self._memes_loaded:
            return False
        return meme_name in self.meme_keywords

    def match_meme_keyword(self, text: str, fuzzy_match: bool) -> str | None:
        if not self._memes_loaded:
            return None
        if fuzzy_match:
            # 模糊匹配：检查关键词是否在消息字符串中
            keyword = next((k for k in self.meme_keywords if k in text), None)
        else:
            # 精确匹配：检查关键词是否等于消息字符串的第一个单词
            keyword = next(
                (k for k in self.meme_keywords if k == text.split()[0]), None
            )
        return keyword

    async def render_meme_list_image(self) -> bytes | None:
        if not self._memes_loaded:
            logger.warning("Memes尚未加载，无法渲染列表")
            return None
        if self.is_py_version:
            meme_list = [(m, MemeProperties(labels=[])) for m in self.memes]
            return self.render_meme_list(
                meme_list=meme_list,  # type: ignore
                text_template="{index}.{keywords}",
                add_category_icon=True,
            ).getvalue()
        else:
            meme_props = {m.key: MemeProperties() for m in self.memes}
            return await asyncio.to_thread(
                self.render_meme_list,
                meme_properties=meme_props,
                exclude_memes=[],
                sort_by=MemeSortBy.KeywordsPinyin,
                sort_reverse=False,
                text_template="{index}. {keywords}",
                add_category_icon=True,
            )

    def get_meme_info(self, keyword: str) -> tuple[str, bytes] | None:
        """
        根据关键词返回 meme 的详情
        返回 (描述文本, 预览图bytes)
        如果未找到，返回 None
        """
        meme = self.find_meme(keyword)
        if not meme:
            return None

        if self.is_py_version:
            p = meme.params_type
            keywords = meme.keywords
            tags = meme.tags
        else:
            p = meme.info.params
            keywords = meme.info.keywords
            tags = meme.info.tags

        # 组装信息字符串
        meme_info = ""
        if meme.key:
            meme_info += f"名称：{meme.key}\n"
        if keywords:
            meme_info += f"别名：{keywords}\n"
        if p.max_images > 0:
            meme_info += (
                f"所需图片：{p.min_images}张\n"
                if p.min_images == p.max_images
                else f"所需图片：{p.min_images}~{p.max_images}张\n"
            )
        if p.max_texts > 0:
            meme_info += (
                f"所需文本：{p.min_texts}段\n"
                if p.min_texts == p.max_texts
                else f"所需文本：{p.min_texts}~{p.max_texts}段\n"
            )
        if p.default_texts:
            meme_info += f"默认文本：{p.default_texts}\n"
        if tags:
            meme_info += f"标签：{list(tags)}\n"
        previewed = meme.generate_preview()
        image: bytes = (
            previewed.getvalue() if isinstance(previewed, io.BytesIO) else previewed
        )
        return meme_info, image

    async def generate_meme(
        self, event: AstrMessageEvent, keyword: str
    ) -> bytes | None:
        # 匹配meme
        meme = self.find_meme(keyword)
        if not meme:
            return
        # 收集参数
        params = meme.params_type if self.is_py_version else meme.info.params
        images, texts, options = await self.collect.collect_params(event, params)

        if self.is_py_version:
            meme_images = [i[1] for i in images]
            return (
                await self.run_sync(meme)(images=meme_images, texts=texts, args=options)
            ).getvalue()
        else:
            meme_images = [self.MemeImage(name=str(i[0]), data=i[1]) for i in images]
            return await asyncio.to_thread(meme.generate, meme_images, texts, options)
