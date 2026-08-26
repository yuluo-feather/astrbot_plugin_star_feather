"""事件身份标识单测：发送者用户标识两级降级（群聊不用会话 ID 兜底）。
——牌灵认人，认人的规矩只写这一遍。"""
from identity import raw_sender_id, resolve_sender_uid


class TestResolveSenderUid:
    """stub 事件：鸭子类型，测两级降级链（群号不兜底）。"""

    @staticmethod
    def _evt(sender_user_id="", getter="", session_id="", origin=""):
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

            @property
            def unified_msg_origin(self):
                return self.uni

        return _Evt()

    def test_l1_official_getter(self):
        evt = self._evt(sender_user_id="", getter="12345", session_id="s", origin="o")
        assert resolve_sender_uid(evt) == "12345"

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
