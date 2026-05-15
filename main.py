import asyncio
import json
import re
from asyncio import Lock
from collections import defaultdict

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register


@register(
    "astrbot_plugin_reply_directly",
    "qa296",
    "让您的 AstrBot 在群聊中变得更加生动和智能！本插件使其可以主动的连续交互。",
    "1.4.6",
    "https://github.com/qa296/astrbot_plugin_reply_directly",
)
class ReplyDirectlyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)

        self.config = config
        # 使用独立的锁，避免逻辑冲突
        self.immersive_lock = Lock()
        self.proactive_lock = Lock()

        # 新的数据结构来存储沉浸式会话
        # key: (group_id, user_id)
        # value: {'context': [], 'timer': asyncio.TimerHandle}
        self.immersive_sessions = {}

        self.active_proactive_timers = {}
        self.group_chat_buffer = defaultdict(list)
        logger.info("ReplyDirectly插件 v1.4.4 加载成功！")
        logger.debug(f"插件配置: {self.config}")

    def _extract_json_from_text(self, text: str) -> str:
        # 优先策略：寻找被 ```json ... ``` 包裹的代码块
        json_block_pattern = r"```json\s*(\{.*?\})\s*```"
        match = re.search(json_block_pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # 备用策略：寻找第一个 { 和最后一个 } 之间的所有内容
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1].strip()

        return ""

    # 辅助函数

    async def _arm_immersive_session(self, event: AstrMessageEvent):
        """当机器人回复后，为目标用户启动一个限时的沉浸式会话。"""
        if not self.config.get("enable_immersive_chat", True):
            return

        group_id = event.get_group_id()
        user_id = event.get_sender_id()

        if not group_id or not user_id:
            return

        session_key = (group_id, user_id)

        async with self.immersive_lock:
            if session_key in self.immersive_sessions:
                self.immersive_sessions[session_key]["timer"].cancel()

            context = []
            try:
                uid = event.unified_msg_origin
                curr_cid = (
                    await self.context.conversation_manager.get_curr_conversation_id(
                        uid
                    )
                )
                if curr_cid:
                    conversation = (
                        await self.context.conversation_manager.get_conversation(
                            uid, curr_cid
                        )
                    )
                    if conversation and conversation.history:
                        context = json.loads(conversation.history)
            except Exception as e:
                logger.error(f"[沉浸式对话] 准备上下文时出错: {e}", exc_info=True)

            timeout = self.config.get("immersive_reply_timeout", 120)
            timer = asyncio.get_running_loop().call_later(
                timeout, self._clear_immersive_session, session_key
            )

            self.immersive_sessions[session_key] = {"context": context, "timer": timer}
            logger.info(
                f"[沉浸式对话] 已为群 {group_id} 的用户 {user_id} 开启了 {timeout}s 的沉浸式会话。"
            )

    def _clear_immersive_session(self, session_key):
        """超时后清理沉浸式会话的回调函数"""
        if session_key in self.immersive_sessions:
            self.immersive_sessions.pop(session_key, None)
            logger.debug(f"[沉浸式对话] 会话 {session_key} 已超时并清理。")

    async def _start_proactive_check(self, group_id: str, unified_msg_origin: str):
        """辅助函数，用于启动或重置一个群组的主动插话检查任务。"""
        async with self.proactive_lock:
            if group_id in self.active_proactive_timers:
                self.active_proactive_timers[group_id].cancel()
                logger.debug(f"[主动插话] 取消了群 {group_id} 的旧计时器。")

            self.group_chat_buffer[group_id].clear()
            task = asyncio.create_task(
                self._proactive_check_task(group_id, unified_msg_origin)
            )
            self.active_proactive_timers[group_id] = task
        logger.debug(f"[主动插话] 已为群 {group_id} 启动/重置了延时检查任务。")

    # 核心任务与钩子（@后触发）

    @filter.after_message_sent()
    async def after_bot_message_sent(self, event: AstrMessageEvent):
        """机器人发送消息后，同时启动主动插话和沉浸式对话的计时器。"""
        if not self.config.get("enable_plugin", True):
            return
        if event.is_private_chat():
            return

        group_id = event.get_group_id()
        sender_id = event.get_sender_id()

        # 如果不是群聊消息，或者消息是机器人自己发的，则直接返回
        if not group_id or sender_id == event.get_self_id():
            return

        # 1. 启动/重置主动插话任务 (针对整个群聊)
        if self.config.get("enable_proactive_reply", True):
            await self._start_proactive_check(
                event.get_group_id(), event.unified_msg_origin
            )

        # 2. 启动/重置沉浸式对话任务 (针对被回复的那个用户)
        await self._arm_immersive_session(event)

    async def _get_persona_info_str(self, unified_msg_origin: str) -> str:
        """
        获取当前会话的人格信息并格式化为字符串。
        """
        try:
            # 1. 获取当前对话的 Conversation 对象
            curr_cid = await self.context.conversation_manager.get_curr_conversation_id(
                unified_msg_origin
            )
            if not curr_cid:
                return ""
            conversation = await self.context.conversation_manager.get_conversation(
                unified_msg_origin, curr_cid
            )
            if not conversation:
                return ""

            # 2. 确定正在使用的人格ID
            persona_id = conversation.persona_id
            if persona_id == "[%None]":  # 用户已显式取消人格
                return ""
            if not persona_id:  # 如果为 None，则使用默认人格
                # 修改点 1: 使用字典方式访问默认人格
                persona_id = self.context.provider_manager.selected_default_persona.get(
                    "name"
                )

            if not persona_id:  # 如果连默认人格都没有，则返回
                return ""

            # 3. 从已加载的人格列表中查找完整的人格对象
            all_personas = self.context.provider_manager.personas

            found_persona = next(
                (p for p in all_personas if p.get("name") == persona_id), None
            )

            if found_persona:
                # 4. 格式化人格信息为字符串

                persona_details = (
                    f"--- 当前人格信息 ---\n"
                    f"名称: {found_persona.get('name', '未知')}\n"
                    f"设定: {found_persona.get('prompt', '无')}\n"
                    f"--- 人格信息结束 ---"
                )
                return persona_details

            return ""
        except Exception as e:
            logger.error(f"[人格获取] 获取人格信息时出错: {e}", exc_info=True)
            return ""

    async def _get_conversation_history(self, unified_msg_origin: str) -> list:
        """
        获取指定会话的完整对话历史记录。
        """
        try:
            curr_cid = await self.context.conversation_manager.get_curr_conversation_id(
                unified_msg_origin
            )
            if not curr_cid:
                return []
            conversation = await self.context.conversation_manager.get_conversation(
                unified_msg_origin, curr_cid
            )
            if conversation and conversation.history:
                return json.loads(conversation.history)
            return []
        except Exception as e:
            logger.error(f"[历史记录获取] 获取对话历史时出错: {e}", exc_info=True)
            return []

    async def _proactive_check_task(self, group_id: str, unified_msg_origin: str):
        """延时任务，在指定时间后检查一次是否需要主动插话。"""
        try:
            delay = self.config.get("proactive_reply_delay", 8)
            await asyncio.sleep(delay)

            chat_history = []
            async with self.proactive_lock:
                if (
                    self.active_proactive_timers.get(group_id)
                    is not asyncio.current_task()
                ):
                    return
                if group_id in self.group_chat_buffer:
                    chat_history = self.group_chat_buffer.pop(group_id, [])

            if not chat_history:
                logger.debug(
                    f"[主动插话] 群 {group_id} 在 {delay}s 内无新消息，任务结束。"
                )
                return

            logger.debug(
                f"[主动插话] 群 {group_id} 计时结束，收集到 {len(chat_history)} 条消息，请求LLM判断。"
            )

            # 调用方法获取人格信息
            # 调用方法获取完整的对话历史
            history = await self._get_conversation_history(unified_msg_origin)
            found_persona = await self._get_persona_info_str(unified_msg_origin)

            formatted_history = "\n".join(chat_history)
            # 将最近的群聊内容作为 prompt
            user_prompt = (
                f"--- 最近的群聊内容 ---\n{formatted_history}\n--- 群聊内容结束 ---"
            )

            instruction = (
                "你需要根据对话历史和人格设定，判断是否要回复以及回复什么。"
                '【1. 输出格式】必须严格返回```json{"should_reply": 布尔值, "content": "字符串"}```的格式，否则无效。'
                "【2. 字段解释】"
                " - `should_reply` (布尔值): 决定你是否想主动发起或继续对话。true表示想，false表示不想。"
                " - `content` (字符串): 你的回复内容。即使不想继续对话(false)，也可以通过提供内容来发表简短、非引导性的评论。"
                "【3. 判断场景】"
                " - **想继续对话 (should_reply: true)**: 当话题有趣、与你相关、或你能提供价值时使用。此时`content`应为具体回复。"
                " - **不想继续对话 (should_reply: false)**: 当话题无关、无聊、或你想结束对话时使用。此时`content`可为空字符串（不回复），或一句简短的结束语/吐槽。"
                " - **情景感知**: 分析“最近群聊内容”判断当前讨论是否已结束或是一个新开端，结合“完整对话历史”理解前因后果，再做出决策。"
            )

            provider = self.context.get_using_provider()
            if not provider:
                logger.warning("[主动插话] 未找到可用的大语言模型提供商。")
                return

            # 在 text_chat 调用中传入 history 作为 contexts
            llm_response = await provider.text_chat(
                prompt=user_prompt, contexts=history, system_prompt=instruction
            )
            # ...
            # 使用您已有的辅助函数来提取JSON字符串
            json_string = self._extract_json_from_text(llm_response.completion_text)

            # 如果未能提取出JSON，则记录并提前返回
            if not json_string:
                logger.warning(
                    f"[主动插话] 从LLM回复中未能提取出JSON。原始回复: {llm_response.completion_text}"
                )
                return
            try:
                decision_data = json.loads(json_string)
                should_reply = decision_data.get("should_reply")
                content = decision_data.get("content", "")

                if should_reply is True:
                    if content:  # true 且有内容才回复
                        logger.info(
                            f"[主动插话] LLM判断需要回复，内容: {content[:50]}..."
                        )
                        message_chain = MessageChain().message(content)
                        await self.context.send_message(
                            unified_msg_origin, message_chain
                        )
                    else:
                        logger.info(
                            "[主动插话] LLM判断为 true 但内容为空，跳过回复，继续计时。"
                        )
                        # ✅ 继续下一次计时
                        await self._start_proactive_check(group_id, unified_msg_origin)
                elif should_reply is False:
                    if content:  # false 但有内容也回复
                        logger.info(
                            f"[主动插话] LLM判断为 false 但仍回复，内容: {content[:50]}..."
                        )
                        message_chain = MessageChain().message(content)
                        await self.context.send_message(
                            unified_msg_origin, message_chain
                        )
                    else:
                        logger.info(
                            "[主动插话] LLM判断为 false 且无内容，跳过回复与计时。"
                        )
                else:
                    logger.warning("[主动插话] LLM返回格式异常，跳过处理。")
            except (json.JSONDecodeError, TypeError, AttributeError) as e:
                logger.error(
                    f"[主动插话] 解析LLM的JSON回复失败: {e}\n原始回复: {llm_response.completion_text}\n清理后文本: '{json_string}'"
                )

        except asyncio.CancelledError:
            logger.debug(f"[主动插话] 群 {group_id} 的检查任务被取消。")
        except Exception as e:
            logger.error(
                f"[主动插话] 群 {group_id} 的检查任务出现未知异常: {e}", exc_info=True
            )
        finally:
            async with self.proactive_lock:
                if self.active_proactive_timers.get(group_id) is asyncio.current_task():
                    self.active_proactive_timers.pop(group_id, None)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """统一处理所有群聊消息，优先处理沉浸式对话。"""

        # 从 event 对象获取事件相关信息
        group_id = event.get_group_id()
        sender_id = event.get_sender_id()

        # 如果不是群聊消息，或者消息是机器人自己发的，则直接返回
        if not group_id or sender_id == event.get_self_id():
            return

        # 获取原始消息（保留前缀）
        raw_message = ""
        try:
            raw_message = str(
                event.message_obj.raw_message.get("raw_message", "")
            ).lstrip()
        except Exception:
            raw_message = event.message_str.lstrip()

        astrbot_config = self.context.get_config()
        command_prefixes = astrbot_config.get("wake_prefix", ["/"])

        # 判断是否以任一指令前缀开头
        if any(raw_message.startswith(prefix) for prefix in command_prefixes):
            logger.debug("[调试] 命中指令前缀，清理并跳过沉浸式")
            session_key = (event.get_group_id(), event.get_sender_id())
            async with self.immersive_lock:
                if session_key in self.immersive_sessions:
                    self.immersive_sessions[session_key]["timer"].cancel()
                    self.immersive_sessions.pop(session_key, None)
            return

        # --- 逻辑1: 检查是否触发了沉浸式对话 ---
        session_key = (group_id, sender_id)
        async with self.immersive_lock:
            session_data = self.immersive_sessions.get(session_key)

        if session_data:
            logger.info(
                f"[沉浸式对话] 捕获到用户 {sender_id} 的连续消息，开始判断是否回复。"
            )

            # 因为要进行沉浸式回复，所以取消可能存在的、针对全群的主动插话任务
            async with self.proactive_lock:
                if group_id in self.active_proactive_timers:
                    self.active_proactive_timers[group_id].cancel()
                    logger.debug(f"[沉浸式对话] 已取消群 {group_id} 的主动插话任务。")

            # 阻止事件继续传播，避免触发默认的LLM回复
            event.stop_event()

            found_persona = await self._get_persona_info_str(event.unified_msg_origin)

            saved_context = session_data.get("context", [])
            user_prompt = event.message_str
            instruction = (
                "你正在与一位用户进行沉浸式对话，需要根据对话历史和人格设定，判断是否要继续回复。"
                '【1. 输出格式】必须严格返回```json{"should_reply": 布尔值, "content": "字符串"}```的格式，否则无效。'
                "【2. 字段解释】"
                " - `should_reply` (布尔值): 决定你是否想主动发起或继续对话。true表示想，false表示不想。"
                " - `content` (字符串): 你的回复内容。即使不想继续对话(false)，也可以通过提供内容来发表简短、非引导性的评论。"
                "【3. 判断场景】"
                " - **想继续对话 (should_reply: true)**: 当用户的话题有趣、与你相关、或你能提供价值时使用。此时`content`应为具体回复。"
                " - **不想继续对话 (should_reply: false)**: 当用户的话题无关、无聊、或你想结束对话时使用。此时`content`可为空字符串(不回复)，或一句简短的结束语。"
                " - **情景感知**: 这是用户的追问，请结合“完整对话历史”理解前因后果，再做出决策。"
            )

            provider = self.context.get_using_provider()
            if not provider:
                return

            llm_response = await provider.text_chat(
                prompt=user_prompt, contexts=saved_context, system_prompt=instruction
            )

            json_string = self._extract_json_from_text(llm_response.completion_text)
            if not json_string:
                logger.warning(
                    f"[沉浸式对话] 从LLM回复中未能提取出JSON。原始回复: {llm_response.completion_text}"
                )
                return

            try:
                decision_data = json.loads(json_string)
                should_reply = decision_data.get("should_reply")
                content = decision_data.get("content", "")

                if should_reply is True:
                    if content:
                        logger.info(
                            f"[沉浸式对话] LLM判断需要回复，内容: {content[:50]}..."
                        )
                        yield event.plain_result(content)
                        # 保存对话历史
                        ...
                    else:
                        logger.info(
                            "[沉浸式对话] LLM判断为 true 但内容为空，跳过回复，继续计时。"
                        )
                        # 触发主动插话计时
                        await self._start_proactive_check(
                            group_id, event.unified_msg_origin
                        )
                elif should_reply is False:
                    if content:
                        logger.info(
                            f"[沉浸式对话] LLM判断为 false 但仍回复，内容: {content[:50]}..."
                        )
                        yield event.plain_result(content)
                    else:
                        logger.info(
                            "[沉浸式对话] LLM判断为 false 且无内容，跳过回复与计时。"
                        )

                    try:
                        conversation_manager = self.context.conversation_manager
                        uid = event.unified_msg_origin
                        curr_cid = await conversation_manager.get_curr_conversation_id(
                            uid
                        )

                        if curr_cid:
                            conversation = await conversation_manager.get_conversation(
                                uid, curr_cid
                            )

                            try:
                                history = json.loads(conversation.history)
                            except Exception as e:
                                logger.warning(f"[沉浸式对话] 解析历史记录失败: {e}")
                                history = []

                            history.append({"role": "user", "content": user_prompt})
                            history.append({"role": "assistant", "content": content})

                            conversation.history = json.dumps(
                                history, ensure_ascii=False
                            )
                            await conversation_manager.update_conversation(
                                uid, curr_cid, history
                            )

                            logger.debug(
                                f"[沉浸式对话] 已追加消息到对话 {curr_cid[:8]}..."
                            )
                        else:
                            logger.warning(
                                f"[沉浸式对话] 未找到当前用户的激活对话，用户: {uid}"
                            )
                    except Exception as db_e:
                        logger.error(f"[沉浸式对话] 保存对话到数据库时出错: {db_e}")

                    yield event.plain_result(content)
                else:
                    logger.info("[沉浸式对话] LLM判断无需回复，尝试触发主动插话。")

                    # 将这条消息加入群聊缓冲区
                    async with self.proactive_lock:
                        if group_id in self.active_proactive_timers:
                            sender_name = event.get_sender_name() or sender_id
                            message_text = event.message_str.strip()
                            if (
                                message_text
                                and len(self.group_chat_buffer[group_id]) < 20
                            ):
                                self.group_chat_buffer[group_id].append(
                                    f"{sender_name}: {message_text}"
                                )

                    # 立即触发一次主动插话检查（模拟机器人“刚发完消息”）
                    await self._start_proactive_check(
                        group_id, event.unified_msg_origin
                    )
            except Exception as e:
                logger.error(f"[沉浸式对话] 解析或处理LLM的JSON时出错: {e}")

            return  # 处理完毕，直接返回

        # --- 逻辑2: 如果没有触发沉浸式对话，则为主动插话功能缓冲消息 ---
        if self.config.get("enable_proactive_reply", True):
            async with self.proactive_lock:
                if group_id in self.active_proactive_timers:
                    sender_name = event.get_sender_name() or sender_id
                    message_text = event.message_str.strip()
                    # 只有当消息非空且缓冲区未满时才添加
                    if message_text and len(self.group_chat_buffer[group_id]) < 20:
                        self.group_chat_buffer[group_id].append(
                            f"{sender_name}: {message_text}"
                        )

    async def terminate(self):
        """插件被卸载/停用时调用，用于清理"""
        logger.info("正在卸载ReplyDirectly插件，取消所有后台任务...")

        async with self.proactive_lock:
            for task in self.active_proactive_timers.values():
                task.cancel()
            self.active_proactive_timers.clear()
            self.group_chat_buffer.clear()

        async with self.immersive_lock:
            for session in self.immersive_sessions.values():
                session["timer"].cancel()
            self.immersive_sessions.clear()

        logger.info("ReplyDirectly插件所有后台任务已清理。")
