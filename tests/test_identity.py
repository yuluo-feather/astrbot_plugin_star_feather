"""事件身份标识单测：发送者用户标识两级降级（群聊不用会话 ID 兜底）。
——牌灵认人，认人的规矩只写这一遍。"""
from data.plugins.astrbot_plugin_star_feather.identity import (
    raw_sender_id,
    resolve_sender_uid,
)


class TestResolveSenderUid:
    """stub 事件：鸭子类型，测两级降级链（群号不兜底）。"""

    @staticmethod
    def _evt(sender_user_id="", getter="", session_id="", origin="", platform=""):
        class _Sender:
            user_id = sender_user_id

        class _Msg:
            sender = _Sender()

        class _Evt:
            message_obj = _Msg()
            uni = origin

            def get_sender_id(self):
                return getter

            def get_session_id(self):
                return session_id

            def get_platform_id(self):
                return platform

            @property
            def unified_msg_origin(self):
                return self.uni

        return _Evt()

    def test_l1_official_getter(self):
        evt = self._evt(sender_user_id="", getter="12345", session_id="s", origin="o")
        assert resolve_sender_uid(evt) == "12345"

    def test_platform_prefix_isolates_same_user_id(self):
        """不同适配器下同一个 user_id 必须落成两个身份（多平台不串数据）。"""
        a = self._evt(getter="123", platform="qq")
        b = self._evt(getter="123", platform="telegram")
        assert resolve_sender_uid(a) == "qq:123"
        assert resolve_sender_uid(b) == "telegram:123"
        assert resolve_sender_uid(a) != resolve_sender_uid(b)

    def test_platform_prefix_is_injective(self):
        """分隔符要转义：``p`` + ``x:y`` 与 ``p:x`` + ``y`` 不能撞成同一个人。"""
        a = self._evt(getter="x:y", platform="p")
        b = self._evt(getter="y", platform="p:x")
        assert resolve_sender_uid(a) == "p:x%3Ay"
        assert resolve_sender_uid(b) == "p%3Ax:y"
        assert resolve_sender_uid(a) != resolve_sender_uid(b)

    def test_escape_char_itself_is_escaped(self):
        """字面量 ``%3A`` 与真分隔符不能撞：``%`` 先转义成 ``%25``。"""
        literal = self._evt(getter="x%3Ay", platform="p")
        separator = self._evt(getter="x:y", platform="p")
        assert resolve_sender_uid(literal) == "p:x%253Ay"
        assert resolve_sender_uid(literal) != resolve_sender_uid(separator)

    def test_l2_raw_int_compat(self):
        # 官方 get_sender_id 只认 str，int 型 QQ 号直接拿空 → 第二层直读兜住
        evt = self._evt(sender_user_id=12345, getter="", session_id="s", origin="o")
        assert resolve_sender_uid(evt) == "12345"

    def test_session_id_not_fallback(self):
        # 群聊里 get_session_id / unified_msg_origin 都是群号，不得作用户标识
        evt = self._evt(getter="", session_id="888", origin="o")
        assert resolve_sender_uid(evt) == ""

    def test_origin_not_fallback(self):
        evt = self._evt(getter="", session_id="", origin="aiocqhttp:group:999")
        assert resolve_sender_uid(evt) == ""

    def test_all_empty(self):
        assert resolve_sender_uid(self._evt(getter="", session_id="", origin="")) == ""

    def test_getter_exception_skipped(self):
        # 某一级抛异常不中断，继续下一级
        class _Sender:
            user_id = ""

        class _Msg:
            sender = _Sender()

        class _Evt:
            message_obj = _Msg()

            def get_sender_id(self):
                raise RuntimeError("boom")

            def get_session_id(self):
                return "777"

        assert resolve_sender_uid(_Evt()) == ""

    def test_raw_sender_id_int(self):
        # int 型 QQ 号：官方 get_sender_id 拿空，第二层直读兜住
        evt = self._evt(sender_user_id=999, getter="", session_id="", origin="")
        assert resolve_sender_uid(evt) == "999"

    def test_raw_sender_id_empty(self):
        # 完全没有 sender 的对象，直读返回空且不抛异常
        assert raw_sender_id(object()) == ""
