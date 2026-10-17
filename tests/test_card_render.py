"""牌面渲染与图片生命周期单测：渲染冒烟、临时图片清理（字体见 test_fonts.py）。
——牌面是牌灵的脸，画不好看怎么见人？"""
import asyncio
import os
import time
import types

import pytest
from data.plugins.astrbot_plugin_star_feather import card_render


def _mk(suit, num, cn, en, up, down):
    """测试用的牌字典：up/down 简化为单关键词（渲染只走 brief，够用）。"""
    return {"suit": suit, "num": num, "cn": cn, "en": en,
            "up": {"keywords": [up], "what": "", "how": ""},
            "down": {"keywords": [down], "what": "", "turn": ""}}


# ---------- 渲染冒烟 ----------
class TestRenderSmoke:
    @pytest.fixture(autouse=True)
    def _reset_font_cache(self):
        from data.plugins.astrbot_plugin_star_feather import fonts
        fonts._FONT_CACHE.clear()
        yield

    def test_render_cards_produces_png(self, tmp_path):
        positions = ["过去", "现在", "未来"]
        picks = [
            {"card": _mk("wands", "13", "权杖王后", "Queen of Wands", "自信魅力", "嫉妒占有"), "upright": True},
            {"card": _mk("cups", "2", "圣杯二", "Two of Cups", "两情相悦", "关系失衡"), "upright": False},
            {"card": _mk("major", "19", "太阳", "The Sun", "成功喜悦", "乌云遮日"), "upright": True},
        ]
        path = card_render.render_cards(positions, picks, "羽时三刻", save_dir=str(tmp_path))
        assert os.path.isfile(path)
        assert path.endswith(".png") and os.path.getsize(path) > 10_000

    def test_background_cache_returns_independent_copy(self):
        """背景缓存必须返回独立副本。

        调用方要在背景上作画，缓存若把本体交出去就等于把缓存当草稿纸——
        第一张卡画上去之后，后续每次渲染都会带着它。
        """
        from PIL import ImageDraw
        card = _mk("major", "19", "太阳", "The Sun", "成功喜悦", "乌云遮日")
        card_render._background_cached.cache_clear()
        first = card_render._build_card_background(card, 680, 1200)
        clean = card_render._build_card_background(card, 680, 1200).tobytes()
        ImageDraw.Draw(first).rectangle([0, 0, 40, 40], fill=(255, 0, 0, 255))
        assert card_render._build_card_background(card, 680, 1200).tobytes() == clean

    def test_card_image_cache_is_pure(self):
        """牌面处理缓存只省重算、不改输出；正逆位是两个缓存键。"""
        card = _mk("major", "19", "太阳", "The Sun", "成功喜悦", "乌云遮日")
        path = card_render._asset_path(card)
        card_render._load_card_image.cache_clear()
        up1, w1, h1 = card_render._load_card_image(path, True)
        down, w2, h2 = card_render._load_card_image(path, False)
        up2, _, _ = card_render._load_card_image(path, True)
        info = card_render._load_card_image.cache_info()
        assert (w1, h1) == (w2, h2)          # 缩放尺寸与正逆无关
        assert up1.tobytes() == up2.tobytes()  # 命中缓存：逐像素不变
        assert info.misses == 2 and info.hits == 1  # 正/逆各占一键，第三次命中

    def test_render_cards_background_drawn_from_this_reading(self, tmp_path, monkeypatch):
        # 背景从本签抽出的牌里随机挑一张（3 张阵=3选1）：绝不挑到本签之外的牌
        seen = []

        def fake_bg(card, w, h):
            seen.append(card)
            from PIL import Image
            return Image.new("RGB", (w, h), (40, 40, 60))

        monkeypatch.setattr(card_render, "_build_card_background", fake_bg)
        # 背景选牌走模块私有 RNG（不消耗全局随机状态），打桩点随之落在它身上
        monkeypatch.setattr(card_render, "_RNG",
                            types.SimpleNamespace(choice=lambda seq: seq[1]))
        positions = ["过去", "现在", "未来"]
        picks = [
            {"card": _mk("major", "13", "死神", "Death", "结束新生", "停滞不前"), "upright": True},
            {"card": _mk("cups", "2", "圣杯二", "Two of Cups", "两情相悦", "关系失衡"), "upright": False},
            {"card": _mk("wands", "1", "权杖一", "Ace of Wands", "灵感火花", "阻碍重重"), "upright": True},
        ]
        path = card_render.render_cards(positions, picks, "羽时三刻", save_dir=str(tmp_path))
        assert os.path.isfile(path)
        assert seen[-1] == picks[1]["card"]  # 随机命中第 2 张时背景就是它
        assert all(c in [p["card"] for p in picks] for c in seen)  # 始终在本签牌内挑

    def test_build_card_background_sizes_and_dim(self):
        # 牌面 cover 做底 + 深藏青遮罩：尺寸吻合、整体压暗（不漏亮底）
        from PIL import Image
        bg = card_render._build_card_background(
            _mk("major", "19", "太阳", "The Sun", "成功喜悦", "乌云遮日"), 120, 80)
        assert isinstance(bg, Image.Image)
        assert bg.size == (120, 80)
        px = bg.getpixel((10, 10))
        assert sum(px[:3]) < 400  # 遮罩后最亮通道也到不了未遮罩的白底亮度

    def test_build_card_background_missing_asset_raises(self):
        import pytest
        with pytest.raises(FileNotFoundError):
            card_render._build_card_background(
                _mk("major", "99", "不存在", "Nope", "无", "无"), 120, 80)

    def test_daily_card_poster_produces_png(self, tmp_path):
        # 今日牌运卡海报：竖版定型尺寸、真实素材可渲染、文件名带 daily 前缀（清理识别用）
        path = card_render._render_daily_card_img(
            _mk("major", "19", "太阳", "The Sun", "成功喜悦", "乌云遮日"), False,
            "别把一时心情当决定，睡一觉再说。", "8月27日·周四", save_dir=str(tmp_path))
        from PIL import Image
        assert os.path.isfile(path)
        assert path.endswith(".png")
        assert Image.open(path).size == (card_render.DAILY_CARD_W, card_render.DAILY_CARD_H)

    def test_daily_card_poster_missing_asset_raises(self, tmp_path):
        # 素材缺失：向调用方抛异常（编排层负责回退，渲染层只保证 fail loudly）
        import pytest
        with pytest.raises(FileNotFoundError):
            card_render._render_daily_card_img(
                _mk("major", "99", "不存在", "Nope", "无", "无"), True,
                "签文", "8月27日·周四", save_dir=str(tmp_path))

    def test_split_signature_lines_by_punctuation(self):
        # 签文分行：优先在标点后断（标点留行尾），单句超宽才逐字回落；不丢字不截断
        from data.plugins.astrbot_plugin_star_feather import fonts
        from PIL import Image, ImageDraw
        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        # 取字不带 text：必中内置子集，字宽因此与平台无关。
        # 第一版本羽写的是 `_load_font(24, text="测")`，「测」不在内置子集里 ——
        # 本机于是回退微软雅黑（宽），CI 的 Linux 上无中文字体、落到 load_default（窄），
        # 同一段 18 字量出 430 与 120 两种宽度，写死的 300px 只在 Windows 上量得够窄。
        # 教训：字宽随「选了哪颗字体」变，凡是拿宽度断言的用例都得当场实测、不许写死像素。
        font = fonts._load_font(24)
        text = "别把一时心情当决定，睡一觉再说。"
        w_full = d.textlength(text, font=font)
        # 生产实参：_render_daily_card_img 传的是「卡宽 - 200」
        max_w_card = card_render.DAILY_CARD_W - 200
        lines = card_render._split_signature_lines(d, text, font, max_w_card)
        assert "".join(lines) == text
        assert len(lines) == 1  # 短签文（约 18 字）：卡宽内保持单行（旧卡观感）
        # 宽度收在「首段宽 … 整句宽」之间：首段放得下、整句放不下 → 必在逗号后断、标点收尾
        w_seg = d.textlength("别把一时心情当决定，", font=font)
        lines_narrow = card_render._split_signature_lines(
            d, text, font, int((w_seg + w_full) / 2))
        assert "".join(lines_narrow) == text
        assert len(lines_narrow) >= 2 and lines_narrow[0].endswith("，")
        # 单句超宽：逐字回落且不丢字（这句无标点，取半句宽必逼出多行）
        long_sig = "这是一句特别长的牌灵的话用来确认换行不丢字也不截断。"
        lines2 = card_render._split_signature_lines(
            d, long_sig, font, int(d.textlength(long_sig, font=font) / 2))
        assert "".join(lines2) == long_sig
        assert len(lines2) >= 2
        # 短签文（18 字内）足够宽松时一行放下（旧卡同款观感）
        lines3 = card_render._split_signature_lines(d, text, font, 1000)
        assert len(lines3) == 1


# ---------- 临时图片生命周期 ----------
class TestTempCleanup:
    def test_delayed_remove(self, tmp_path):
        f = tmp_path / "tarot_test.png"
        f.write_bytes(b"x")
        asyncio.run(card_render._delayed_remove(str(f), 0))
        assert not f.exists()

    def test_delayed_remove_missing_ok(self):
        # 文件已被处理：删除不存在的文件不抛异常
        asyncio.run(card_render._delayed_remove("C:/not/exist/tarot_x.png", 0))

    def test_schedule_cleanup_no_loop_safe(self):
        # 无事件循环环境（同步测试）：静默跳过，不抛异常
        card_render._schedule_image_cleanup("C:/not/exist/tarot_x.png")
        card_render._schedule_image_cleanup(None)

    def test_cleanup_task_is_referenced_until_it_finishes(self, tmp_path):
        """任务必须留强引用：asyncio 只持弱引用，没人引用时可能被 GC，保险丝静默失效。"""
        f = tmp_path / "tarot_keep.png"
        f.write_bytes(b"x")

        async def go():
            card_render._schedule_image_cleanup(str(f), delay=0)
            assert card_render._CLEANUP_TASKS, "任务应被集合引用"
            await asyncio.sleep(0.05)
            return not card_render._CLEANUP_TASKS

        assert asyncio.run(go())
        assert not f.exists()

    def test_ttl_is_one_number_and_it_is_a_fuse(self):
        """保险丝时长只有一份，且不许回落到「牌灵的话」都能跑过的量级。

        2026-09-29 实测：一句「牌灵的话」的 AI 请求自己跑了 63 秒——旧默认 30 秒
        正好在那个窗口里把图删了，平台读到空，整条消息链被打回（Issue #2）。
        正确性不靠定时器（发送前核验在 main._ensure_image），但把这里调回 30 秒
        只会让「图提前消失」重新回到日常路径，所以钉个下界；同时断言两个函数的
        默认值与常量同源：不许有人就地写回一个 30 / 300。
        """
        import inspect
        assert card_render.IMAGE_TTL_SECONDS >= 120, "短于实测故障窗口（63 秒）的量级"
        for fn in (card_render._delayed_remove, card_render._schedule_image_cleanup):
            assert inspect.signature(fn).parameters["delay"].default == card_render.IMAGE_TTL_SECONDS

class TestStaleCleanup:
    """启动清理（①实例隔离的配套动作）：只删「命名合规 + 确认过期」的残骸。

    旧用例断言的是「父目录里所有 tarot_*.png 一律删」——那正是被修掉的行为：
    新实例启动时把旧实例还在等发送的图删掉，与 Issue #2 同一条死法。
    所以这一组把三条新契约逐条钉住：新鲜的一律不碰、不合规的一律不碰、
    别人的过期目录才回收（自己的免死）。
    """

    @staticmethod
    def _root(tmp_path, monkeypatch):
        """把根目录换到 tmp_path。

        注意打在模块常量上而不是 tempfile.gettempdir：IMAGE_DIR_ROOT 是导入时
        算好的字符串，再改 tempfile 已经晚了（旧用例就是那样打空的——它当时能
        过只是因为旧实现恰好删了真目录里的东西）。
        """
        root = tmp_path / "star_feather"
        root.mkdir()
        monkeypatch.setattr(card_render, "IMAGE_DIR_ROOT", str(root))
        return root

    @staticmethod
    def _age(path, seconds):
        old = time.time() - seconds
        os.utime(path, (old, old))

    def test_fresh_files_survive(self, tmp_path, monkeypatch):
        """刚写出来的图必须活着：旧实例可能还在等平台取走它。"""
        root = self._root(tmp_path, monkeypatch)
        fresh = [root / "tarot_a.png", root / "daily_b.png"]
        for p in fresh:
            p.write_bytes(b"img")
        card_render.cleanup_stale_images()
        assert all(p.exists() for p in fresh)

    def test_stale_scattered_pngs_removed_but_irregular_kept(self, tmp_path, monkeypatch):
        """旧布局散图过期才删；不合规的文件哪怕放烂了也不动（父目录不是自家后院）。"""
        root = self._root(tmp_path, monkeypatch)
        stale_png = root / "tarot_old.png"
        stale_png.write_bytes(b"img")
        junk = root / "keep.txt"
        junk.write_bytes(b"keep")
        odd = root / "tarot_old.jpg"
        odd.write_bytes(b"img")
        for p in (stale_png, junk, odd):
            self._age(p, card_render.STALE_INSTANCE_DIR_AGE + 60)
        card_render.cleanup_stale_images()
        assert not stale_png.exists()
        assert junk.exists() and odd.exists()

    def test_stale_instance_dir_reaped_fresh_kept(self, tmp_path, monkeypatch):
        """实例目录按同一条过期判据回收：三天没写过新东西才动它。"""
        root = self._root(tmp_path, monkeypatch)
        stale = root / "inst_4242_1_deadbeef"
        fresh = root / "inst_4242_2_cafebabe"
        for d in (stale, fresh):
            d.mkdir()
            (d / "tarot_x.png").write_bytes(b"img")
        self._age(stale, card_render.STALE_INSTANCE_DIR_AGE + 60)
        card_render.cleanup_stale_images()
        assert not stale.exists()
        assert fresh.exists() and (fresh / "tarot_x.png").exists()

    def test_own_dir_never_reaped(self, tmp_path, monkeypatch):
        """自己的目录免死：时钟被往回调也不许删——里面的图可能正被发送。"""
        root = self._root(tmp_path, monkeypatch)
        mine = root / "inst_4242_3_0badc0de"
        mine.mkdir()
        (mine / "tarot_mine.png").write_bytes(b"img")
        self._age(mine, card_render.STALE_INSTANCE_DIR_AGE * 10)
        card_render.cleanup_stale_images(str(mine))
        assert (mine / "tarot_mine.png").exists()

    def test_same_pid_stale_non_empty_dir_kept(self, tmp_path, monkeypatch):
        """同进程的另一代实例目录只要不是空壳就免删：热重载的新实例不许回收旧实例
        还在等发送的图。

        这是①最危险的那一类：名字合规、确实过期、而且 pid 就是本进程——旧写法只跳过
        传入的 current_dir，于是刚出生的新实例会把隔壁老实例的目录整个删掉
        （2026-10-13 外部复现已确认）。判据取 PID 解析 + 目录非空即保留。
        """
        root = self._root(tmp_path, monkeypatch)
        old = root / f"inst_{os.getpid()}_1_deadbeef"
        new = root / f"inst_{os.getpid()}_2_cafebabe"
        for d in (old, new):
            d.mkdir()
            (d / "tarot_x.png").write_bytes(b"img")
        self._age(old, card_render.STALE_INSTANCE_DIR_AGE * 10)
        card_render.cleanup_stale_images(str(new))   # 只传自己：旧实例目录仍须免删
        assert old.exists() and (old / "tarot_x.png").exists()
        assert new.exists()

    def test_same_pid_empty_stale_dir_reaped(self, tmp_path, monkeypatch):
        """同进程的旧实例目录只剩空壳时按过期回收：否则每次热重载都留下一个空目录，
        进程不退出就永远清不掉。"""
        root = self._root(tmp_path, monkeypatch)
        old = root / f"inst_{os.getpid()}_1_deadbeef"
        new = root / f"inst_{os.getpid()}_2_cafebabe"
        for d in (old, new):
            d.mkdir()
        self._age(old, card_render.STALE_INSTANCE_DIR_AGE * 10)
        card_render.cleanup_stale_images(str(new))
        assert not old.exists(), "同进程的空壳过期目录应被回收"
        assert new.exists(), "当前实例目录永不删"

    def test_same_pid_empty_fresh_dir_kept(self, tmp_path, monkeypatch):
        """同进程、空、但没过期：可能是旧实例刚建出来还没渲染，别动。"""
        root = self._root(tmp_path, monkeypatch)
        old = root / f"inst_{os.getpid()}_1_deadbeef"
        new = root / f"inst_{os.getpid()}_2_cafebabe"
        for d in (old, new):
            d.mkdir()
        card_render.cleanup_stale_images(str(new))
        assert old.exists(), "没过期的空目录不许回收"

    def test_same_pid_empty_current_dir_kept(self, tmp_path, monkeypatch):
        """当前实例目录哪怕空且过期也不删：时钟被往回调时它可能正要落第一张图。"""
        root = self._root(tmp_path, monkeypatch)
        mine = root / f"inst_{os.getpid()}_3_0badc0de"
        mine.mkdir()
        self._age(mine, card_render.STALE_INSTANCE_DIR_AGE * 10)
        card_render.cleanup_stale_images(str(mine))
        assert mine.exists()

    def test_other_pid_stale_dir_is_still_reaped(self, tmp_path, monkeypatch):
        """反向一格：别的 PID 的过期目录照旧回收——① 改的是豁免面，不是停掉回收。"""
        root = self._root(tmp_path, monkeypatch)
        foreign = root / f"inst_{os.getpid() + 1}_1_deadbeef"
        foreign.mkdir()
        (foreign / "tarot_x.png").write_bytes(b"img")
        self._age(foreign, card_render.STALE_INSTANCE_DIR_AGE * 10)
        card_render.cleanup_stale_images()
        assert not foreign.exists()

    def test_irregular_names_untouched(self, tmp_path, monkeypatch):
        """命名不合规的目录/文件一律不碰：判据要能挡住手改、截断、别的程序。"""
        root = self._root(tmp_path, monkeypatch)
        odd_dir = root / "inst_abc"
        odd_dir.mkdir()
        (odd_dir / "tarot_x.png").write_bytes(b"img")
        short_hex = root / "inst_1_1_deadbee"          # 7 位十六进制
        short_hex.mkdir()
        self._age(odd_dir, card_render.STALE_INSTANCE_DIR_AGE * 10)
        self._age(short_hex, card_render.STALE_INSTANCE_DIR_AGE * 10)
        card_render.cleanup_stale_images()
        assert odd_dir.exists() and short_hex.exists()

    def test_missing_root_is_noop(self, tmp_path, monkeypatch):
        """根目录不存在（装机首启）：静默返回，不抛也不建。"""
        monkeypatch.setattr(card_render, "IMAGE_DIR_ROOT", str(tmp_path / "nope"))
        card_render.cleanup_stale_images()


class TestInstanceImageDir:
    """实例目录命名（①的承重墙）：同一进程内两代实例必须不同名。

    热重载正是「同 pid、两代实例」——只靠 pid 区分不了，靠的正是进程内序号。
    """

    def test_two_instances_never_share_a_name(self, tmp_path, monkeypatch):
        root = tmp_path / "star_feather"
        monkeypatch.setattr(card_render, "IMAGE_DIR_ROOT", str(root))
        a = card_render.new_instance_image_dir()
        b = card_render.new_instance_image_dir()
        assert a != b, "同进程两代实例拿到同一个目录＝新实例会踩旧实例的图"
        for path in (a, b):
            assert os.path.isdir(path)
            assert card_render._INSTANCE_DIR_RE.match(os.path.basename(path))
            assert os.path.dirname(path) == str(root)

    def test_regex_rejects_same_pid_only_names(self):
        """判据本身：光有 pid 的名字不是合法实例目录（那正是分不开的那一类）。"""
        assert card_render._INSTANCE_DIR_RE.match("inst_123_1_0a1b2c3d")
        assert not card_render._INSTANCE_DIR_RE.match("inst_123")
        assert not card_render._INSTANCE_DIR_RE.match("inst_123_")
        assert not card_render._INSTANCE_DIR_RE.match("inst_123_1_0A1B2C3D")  # 大写不是本格式

    def test_core_and_daily_share_the_instance_dir(self, tmp_path, monkeypatch):
        """装配口径：核心开目录，今日牌运卡跟着用同一个——别落到共享父目录去。"""
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        from data.plugins.astrbot_plugin_star_feather.tarot_core import StarTarot
        from stubs import FakeContext

        monkeypatch.setattr(card_render, "IMAGE_DIR_ROOT", str(tmp_path / "star_feather"))
        a = StarTarot(FakeContext(None), None)
        b = StarTarot(FakeContext(None), None)
        assert a.image_dir != b.image_dir, "两代实例拿到同一个目录＝①没生效"
        assert DailyFortune(FakeContext(None), a).image_dir == a.image_dir

    def test_root_failure_uses_a_private_unique_dir(self, tmp_path, monkeypatch):
        """根目录建不出来时不退回共享目录：退路也必须是「每实例一份」（①的严格保证）。

        旧写法退回 `tempfile.gettempdir()` 或共享根目录，两代实例又挤回同一处——
        ①修的正是这个，异常分支不能把它放回去。
        """
        broken = tmp_path / "blocked"
        sys_tmp = tmp_path / "sys_tmp"
        sys_tmp.mkdir()
        monkeypatch.setattr(card_render, "IMAGE_DIR_ROOT", str(broken))
        monkeypatch.setattr(card_render.tempfile, "gettempdir", lambda: str(sys_tmp))
        real_makedirs = os.makedirs

        def fail_root(path, *a, **kw):
            if os.path.abspath(str(path)) == os.path.abspath(str(broken)):
                raise OSError("模拟根目录不可写")
            return real_makedirs(path, *a, **kw)
        monkeypatch.setattr(os, "makedirs", fail_root)

        a = card_render.new_instance_image_dir()
        b = card_render.new_instance_image_dir()
        assert a and b and a != b, "退路也必须每实例一份，不许两代共用"
        assert os.path.isdir(a) and os.path.isdir(b)
        assert os.path.abspath(a) != os.path.abspath(str(broken))

    def test_all_fallbacks_failing_degrades_to_no_image(self, tmp_path, monkeypatch):
        """连唯一临时目录都建不出来：返回空串＝降级为无图，绝不悄悄改回共享目录。"""
        monkeypatch.setattr(card_render, "IMAGE_DIR_ROOT", str(tmp_path / "blocked"))

        def boom(*a, **kw):
            raise OSError("磁盘不可用")
        monkeypatch.setattr(os, "makedirs", boom)
        monkeypatch.setattr(card_render.tempfile, "mkdtemp", boom)
        assert card_render.new_instance_image_dir() == ""

    def test_daily_keeps_empty_image_dir_instead_of_shared_root(self, tmp_path, monkeypatch):
        """空路径是「本次降级为无图」的信号：不许用 `or` 换成共享根目录（①的边界）。"""
        from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
        monkeypatch.setattr(card_render, "IMAGE_DIR_ROOT", str(tmp_path / "star_feather"))
        tarot = types.SimpleNamespace(image_dir="")
        assert DailyFortune(None, tarot).image_dir == ""


class TestReloadIsolationEndToEnd:
    """① 端到端：新实例启动清理不许回收同进程旧实例仍在用的图（2026-10-13 第 4 条补充）。

    危险组合 ＝ 同 PID + 多实例 + 旧目录已过回收年龄，三者缺一都测不出真问题：
    - 只测新鲜目录：旧实现（本批修之前）在 mtime 未过期时本来就不删，假绿；
    - 只测别的 PID 的过期目录：那是本就该回收的重启残骸，与本案无关；
    - 同 PID 但目录新鲜：同样走不到删除分支。
    所以本组把 A 的目录 mtime 直接压到阈值之外（os.utime 造过期，不真等三天），
    再让 B 走一遍 StarFeatherPlugin.__init__ 里那句启动清理，断言 A 的图与目录
    都还在、还打得开。普通牌面图与今日海报各一格（参数化）。
    """

    @staticmethod
    def _pair(tmp_path, monkeypatch):
        """同进程两代实例（真 StarTarot：image_dir 由 new_instance_image_dir 开出）。"""
        from data.plugins.astrbot_plugin_star_feather.tarot_core import StarTarot
        from stubs import FakeContext
        monkeypatch.setattr(card_render, "IMAGE_DIR_ROOT", str(tmp_path / "star_feather"))
        return StarTarot(FakeContext(None), None), StarTarot(FakeContext(None), None)

    @staticmethod
    def _age(path, seconds):
        old = time.time() - seconds
        os.utime(path, (old, old))

    @pytest.mark.parametrize("kind", ["plain", "daily"])
    def test_previous_instance_image_survives_reload(self, tmp_path, monkeypatch, kind):
        from PIL import Image
        a, b = self._pair(tmp_path, monkeypatch)
        assert a.image_dir != b.image_dir, "两代实例必须各自一份目录"
        positions, picks = a._draw("羽签")            # 真抽牌
        if kind == "plain":
            img = card_render.render_cards(positions, picks, "羽签", save_dir=a.image_dir)
        else:
            pick = picks[0]
            img = card_render._render_daily_card_img(pick["card"], bool(pick["upright"]),
                                                     "心之所向，自有回响。", "10月13日·周一",
                                                     save_dir=a.image_dir)
        assert os.path.isfile(img), "A 得先真渲染出一张图，后面才谈得上「被误删」"
        self._age(a.image_dir, card_render.STALE_INSTANCE_DIR_AGE + 60)

        card_render.cleanup_stale_images(b.image_dir)   # StarFeatherPlugin.__init__ 的那一行

        assert os.path.isdir(a.image_dir), "同 PID 旧实例目录被新实例回收了"
        assert os.path.isfile(img), "旧实例仍在用的图被删了"
        with Image.open(img) as im:                     # 「A 仍能使用该图片」
            im.verify()


class TestWrapAndSignatureLines:
    """换行与签文分行：短路等价性 + 单字超宽的终止性（此前两者都零覆盖）。"""

    @staticmethod
    def _draw():
        from PIL import Image, ImageDraw
        return ImageDraw.Draw(Image.new("RGB", (8, 8)))

    def test_wrap_text_single_line_when_fits(self):
        d = self._draw()
        font = card_render._load_font(22)
        text = "短牌义"
        assert d.textlength(text, font=font) <= 480
        assert card_render._wrap_text(d, text, font, 480) == [text]

    def test_wrap_text_empty_returns_no_line(self):
        assert card_render._wrap_text(self._draw(), "", card_render._load_font(22), 480) == []

    def test_wrap_text_splits_when_too_wide(self):
        d = self._draw()
        long_text = "很长的牌义关键词串" * 3
        lines = card_render._wrap_text(d, long_text, card_render._load_font(22), 60)
        assert len(lines) > 1 and "".join(lines) == long_text  # 拆行不丢字

    def test_signature_line_oversized_glyph_terminates(self):
        """单字宽于画布必须有限步结束：旧实现 cut 恒 0、seg 长度不减 → 死循环，
        而本函数跑在 to_thread 里，触发即静默挂死一个线程池槽位。"""
        from PIL import Image, ImageDraw
        d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        out = card_render._split_signature_lines(d, "字字字", card_render._load_font(24), 1)
        assert "".join(out) == "字字字"


class TestDailyCardLayoutInvariants:
    """海报卡几何不变量：签文引用块必须落在卡片下方、水印上方，间距可辨。

    卡底 = DAILY_CARD_Y + CARD_H，而 CARD_H 由 PAD_X / INNER_H / INFO_H 三者决定，
    改任一都会移动卡底。签文位置若写死数值就与卡片重叠或拉出大空洞——旧版正是
    字面量 992（= 176 + 748 + 68 的产物）。这里按推导关系断言，把「签文起点跟着
    卡片高度动」这条契约钉住：后人调高信息区却没让签文跟随时，第一条断言即红。
    """

    def test_card_height_is_derived_not_literal(self):
        from data.plugins.astrbot_plugin_star_feather.card_render import (
            CARD_H,
            INFO_H,
            INNER_H,
            PAD_X,
        )
        assert CARD_H == PAD_X * 2 + INNER_H + INFO_H

    def test_quote_block_sits_between_card_and_watermark(self):
        from data.plugins.astrbot_plugin_star_feather.card_render import (
            CARD_H,
            DAILY_CARD_H,
            DAILY_CARD_QUOTE_Y,
            DAILY_CARD_Y,
        )
        card_bottom = DAILY_CARD_Y + CARD_H
        assert card_bottom < DAILY_CARD_QUOTE_Y, "签文块与卡片重叠"
        assert DAILY_CARD_QUOTE_Y < DAILY_CARD_H - 58, "签文块压到水印上"
        gap = DAILY_CARD_QUOTE_Y - card_bottom
        assert 30 <= gap <= 120, f"卡底到签文间距 {gap}px 超出可辨区间（过小重叠、过大空洞）"


class TestDrawCardFontReuse:
    """同一张牌里同字号的字体只取一次：量宽与逐行绘制必须共用同一字体对象。

    旧版对 size=22 取了两次（量宽一次、逐行绘制一次），而两次调用能不能拿到同一个
    字体只是**隐式前提**（_font_covers 基于不同文本各自判定），不是保证。共用一个
    局部变量之后，「量的时候放得下、画的时候溢出」在结构上就不可能发生了。
    """

    def test_same_size_loaded_once_per_card(self, monkeypatch):
        from data.plugins.astrbot_plugin_star_feather import card_render
        from data.plugins.astrbot_plugin_star_feather.tarot_data import TAROT_CARDS
        from PIL import Image

        calls = []
        orig = card_render._load_font

        def spy(size, bold=False, text=None):
            calls.append((size, bold))
            return orig(size, bold, text=text)

        canvas = Image.new("RGB", (card_render.CARD_OUT_W + 40, card_render.CARD_H + 40),
                           (250, 250, 250))
        monkeypatch.setattr(card_render, "_load_font", spy)
        card_render._draw_card(canvas, 20, 20, TAROT_CARDS[0], True)
        n22 = calls.count((22, False))
        assert n22 == 1, f"牌义字号被取了 {n22} 次（应 1 次）：{calls}"
        assert len(calls) == len(set(calls)), f"同一次绘制里有重复字号：{calls}"


class TestBackgroundCacheSizing:
    """背景缓存的 key 含牌面信息，maxsize 语义是「最近 N 个 (牌, 尺寸) 组合」。

    底牌必须跟本签的牌走（改成只按尺寸缓存会让不同签共用同一张底牌），代价是
    **跨签命中率低**——每次渲染只挑一张底牌进缓存，遇到 4 张不同的牌（同尺寸）
    才填满。这里锁两件事：容量不小于最大牌阵的候选牌数、同 key 第二次必命中
    （缓存机制本身没坏）。注意别写成「同一签连渲两次必命中」——第一次只进一张，
    第二次随机挑到别张就是 miss，那个断言不成立（我写错过一次）。
    """

    def test_maxsize_covers_largest_spread(self):
        from data.plugins.astrbot_plugin_star_feather import card_render
        assert card_render._background_cached.cache_info().maxsize >= 4, (
            "maxsize 小于最大牌阵的候选底牌数：填满缓存所需的渲染次数会超过牌阵规模")

    def test_same_key_hits_second_time(self):
        from data.plugins.astrbot_plugin_star_feather import card_render
        from data.plugins.astrbot_plugin_star_feather.tarot_data import TAROT_CARDS
        card_render._background_cached.cache_clear()
        c = TAROT_CARDS[0]
        args = (c["suit"], c["num"], c["cn"], 1148, 986)
        card_render._background_cached(*args)
        i1 = card_render._background_cached.cache_info()
        card_render._background_cached(*args)
        i2 = card_render._background_cached.cache_info()
        assert i2.hits == i1.hits + 1 and i2.misses == i1.misses, f"同 key 未命中：{i1} → {i2}"


# ---------- 随机卫生：渲染不掺和全局随机序 ----------
class TestBackgroundRandomHygiene:
    def test_background_pick_leaves_global_random_untouched(self, tmp_path):
        """背景选牌走模块私有 RNG：渲染不消耗全局随机状态。

        全局那位有明确用处（牌灵的话、收尾句这些「抽到谁都行」的调用方），
        渲染掺进去会让「同输入同 md5」的确定性对比失去可复现性——那是本项目
        常用的验收手段（纯表达重构只认渲染逐位不变）。
        """
        import random
        positions = ["过去", "现在", "未来"]
        picks = [
            {"card": _mk("wands", "13", "权杖王后", "Queen of Wands", "自信魅力", "嫉妒占有"), "upright": True},
            {"card": _mk("cups", "2", "圣杯二", "Two of Cups", "两情相悦", "关系失衡"), "upright": False},
            {"card": _mk("major", "19", "太阳", "The Sun", "成功喜悦", "乌云遮日"), "upright": True},
        ]
        random.seed(20260913)
        before = random.getstate()
        path = card_render.render_cards(positions, picks, "羽时三刻", save_dir=str(tmp_path))
        assert os.path.isfile(path)
        assert random.getstate() == before      # 全局随机序一模一样：一次都没被消耗
