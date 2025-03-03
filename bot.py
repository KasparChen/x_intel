import json
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.error import TelegramError

from config import TELEGRAM_TOKEN, ADMIN_HANDLES, DEFAULT_SUMMARY_CYCLE
from s3_storage import append_to_mempool, save_published_message, list_s3_files, load_from_s3, save_to_s3
from llm_agent import analyze_messages
from utils import log_info, log_error, get_timestamp, format_summary


class CryptoBot:
    def __init__(self):
        """初始化 Bot 配置，从 S3 加载持久化数据"""
        self.admins = self.load_config("admins") or ADMIN_HANDLES
        self.receive_channels = self.load_config("receive_channels") or []  # (chat_id, name) 元组
        self.review_channel = self.load_config("review_channel")  # str
        self.publish_channel = self.load_config("publish_channel")  # str
        self.review_enabled = self.load_config("review_enabled") if self.load_config("review_enabled") is not None else True
        self.summary_cycle = self.load_config("summary_cycle") or DEFAULT_SUMMARY_CYCLE
        self.last_position = self.load_config("last_position") or "2025-03-03 00:00:00"
        self.last_message_id = None  # 记录上一条消息 ID

    def is_admin(self, username):
        """检查用户是否为管理员"""
        return f"@{username}" in self.admins

    def update_status(self, status):
        """更新状态，仅记录到日志和后端"""
        log_info(f"Bot 状态: {status}")
        print(f"Bot 状态: {status}")

    def load_config(self, key):
        """从 S3 加载配置"""
        data = load_from_s3("config", f"{key}.json")
        return data.get("value") if data else None

    def save_config(self, key, value):
        """保存配置到 S3"""
        save_to_s3({"value": value}, "config", f"{key}.json")
        log_info(f"配置保存: {key} = {value}")

    async def update_receive_channels(self, application: Application):
        """动态更新消息接收频道的处理器"""
        for handler in application.handlers.get(0, []):
            if isinstance(handler, MessageHandler) and handler.callback == self.receive_message:
                application.remove_handler(handler)
                break
        if self.receive_channels:
            application.add_handler(
                MessageHandler(filters.Chat([int(cid) for cid, _ in self.receive_channels]), self.receive_message)
            )
        log_info(f"已更新接收频道: {[cid for cid, _ in self.receive_channels]}")

    async def delete_last_message(self, context: ContextTypes.DEFAULT_TYPE, chat_id):
        """删除上一条消息"""
        if self.last_message_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=self.last_message_id)
            except TelegramError:
                pass  # 忽略删除失败
            self.last_message_id = None

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """显示主菜单"""
        username = update.effective_user.username
        if not self.is_admin(username):
            await update.message.reply_text("无权限，仅限管理员访问")
            return
        self.update_status("运行中 - 管理菜单已打开")
        keyboard = [
            [InlineKeyboardButton("查询接收频道", callback_data="query_receive_channel")],
            [InlineKeyboardButton("开启审核 🟡" if self.review_enabled else "开启审核", callback_data="enable_review"),
             InlineKeyboardButton("关闭审核" if self.review_enabled else "关闭审核 🔵", callback_data="disable_review")],
            [InlineKeyboardButton("查询管理员", callback_data="query_admin")],
            [InlineKeyboardButton("设置审核频道", callback_data="set_review_channel"),
             InlineKeyboardButton("设置发布频道", callback_data="set_publish_channel"),
             InlineKeyboardButton("设置周期", callback_data="set_cycle")]
        ]
        await self.delete_last_message(context, update.message.chat_id)
        message = await update.message.reply_text("管理菜单", reply_markup=InlineKeyboardMarkup(keyboard))
        self.last_message_id = message.message_id

    async def get_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """获取当前群组或频道 ID"""
        chat_id = update.message.chat_id
        self.update_status(f"运行中 - 获取 ID: {chat_id}")
        try:
            chat = await context.bot.get_chat(chat_id)
            await update.message.reply_text(f"当前群组/频道 ID: {chat_id} (名称: {chat.title})")
        except TelegramError as e:
            await update.message.reply_text(f"当前群组/频道 ID: {chat_id} (无法获取名称: {str(e)})")

    async def summarize(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """主动触发消息总结"""
        username = update.effective_user.username
        if not self.is_admin(username):
            await update.message.reply_text("无权限，仅限管理员访问")
            return
        self.update_status("运行中 - 主动总结请求")
        keyboard = [
            [InlineKeyboardButton("不重置周期计时", callback_data="summarize_no_reset"),
             InlineKeyboardButton("重置周期计时", callback_data="summarize_reset")],
            [InlineKeyboardButton("返回", callback_data="back")]
        ]
        await self.delete_last_message(context, update.message.chat_id)
        message = await update.message.reply_text("选择总结选项：", reply_markup=InlineKeyboardMarkup(keyboard))
        self.last_message_id = message.message_id

    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理按钮点击"""
        query = update.callback_query
        await query.answer()
        data = query.data
        username = query.from_user.username
        if not self.is_admin(username):
            await query.edit_message_text("无权限，仅限管理员访问")
            return

        self.update_status(f"运行中 - 处理按钮: {data}")

        if data == "query_receive_channel":
            await self.query_receive_channel(update, context)
        elif data == "add_receive_channel":
            await self.add_receive_channel_prompt(update, context)
        elif data == "remove_receive_channel":
            await self.remove_receive_channel_prompt(update, context)
        elif data.startswith("remove_ch_"):
            idx = int(data.split("_")[2])
            if 0 <= idx < len(self.receive_channels):
                chat_id, name = self.receive_channels.pop(idx)
                self.save_config("receive_channels", self.receive_channels)
                log_info(f"已移除接收频道: {chat_id} ({name})")
                await query.message.reply_text(f"已解除 {name}({chat_id}) 的监控任务")
                await self.update_receive_channels(context.application)
            await self.query_receive_channel(update, context)  # 刷新
        elif data == "enable_review":
            self.review_enabled = True
            self.save_config("review_enabled", True)
            log_info("审核已开启")
            await query.edit_message_text("审核已开启")
            await self.start(update, context)  # 刷新主菜单
        elif data == "disable_review":
            self.review_enabled = False
            self.save_config("review_enabled", False)
            log_info("审核已关闭")
            await query.edit_message_text("审核已关闭")
            await self.start(update, context)  # 刷新主菜单
        elif data == "query_admin":
            await self.query_admin(update, context)
        elif data == "add_admin":
            await self.add_admin_prompt(update, context)
        elif data == "remove_admin":
            await self.remove_admin_prompt(update, context)
        elif data.startswith("remove_admin_"):
            idx = int(data.split("_")[2])
            if 0 <= idx < len(self.admins):
                admin = self.admins.pop(idx)
                self.save_config("admins", self.admins)
                log_info(f"已移除管理员: {admin}")
                await query.message.reply_text(f"已移除管理员: {admin}")
            await self.query_admin(update, context)  # 刷新
        elif data == "set_review_channel":
            await self.set_review_channel_prompt(update, context)
        elif data == "set_publish_channel":
            await self.set_publish_channel_prompt(update, context)
        elif data == "set_cycle":
            await self.set_cycle_prompt(update, context)
        elif data == "summarize_no_reset":
            await self.summarize_cycle(context)
            await query.edit_message_text("总结完成，未重置周期计时")
            await self.start(update, context)
        elif data == "summarize_reset":
            await self.summarize_cycle(context)
            context.job_queue.run_repeating(self.summarize_cycle, interval=self.summary_cycle * 60, first=0)
            await query.edit_message_text("总结完成，已重置周期计时")
            await self.start(update, context)
        elif data == "back":
            await self.start(update, context)
        elif data.startswith("approve_"):
            summary = context.bot_data.get(data)
            if summary and self.publish_channel:
                await context.bot.send_message(self.publish_channel, summary)
                save_published_message({"content": summary, "timestamp": get_timestamp()})
                log_info(f"审核通过并发布: {summary[:20]}...")
            await query.edit_message_text("已通过并发布")
        elif data.startswith("reject_"):
            await query.edit_message_text("已驳回")

    async def query_receive_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """查询接收频道"""
        self.update_status("运行中 - 查询接收频道")
        channel_list = "\n".join([f"[{i}] {name}" for i, (_, name) in enumerate(self.receive_channels)])
        display_text = f"当前正在监控的信息频道为：\n{channel_list if channel_list else '无'}"
        keyboard = [
            [InlineKeyboardButton("增加接收频道", callback_data="add_receive_channel"),
             InlineKeyboardButton("移除接收频道", callback_data="remove_receive_channel")],
            [InlineKeyboardButton("刷新", callback_data="query_receive_channel")],
            [InlineKeyboardButton("返回", callback_data="back")]
        ]
        await self.delete_last_message(context, update.callback_query.message.chat_id)
        message = await update.callback_query.message.reply_text(display_text, reply_markup=InlineKeyboardMarkup(keyboard))
        self.last_message_id = message.message_id

    async def add_receive_channel_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """提示增加接收频道"""
        self.update_status("运行中 - 增加接收频道")
        channel_list = "\n".join([f"[{i}] {name}" for i, (_, name) in enumerate(self.receive_channels)])
        display_text = f"当前正在监控的信息频道为：\n{channel_list if channel_list else '无'}\n请输入新的 channel ID 如: -184301982"
        keyboard = [[InlineKeyboardButton("返回", callback_data="query_receive_channel")]]
        await self.delete_last_message(context, update.callback_query.message.chat_id)
        message = await update.callback_query.message.reply_text(display_text, reply_markup=InlineKeyboardMarkup(keyboard))
        self.last_message_id = message.message_id
        context.user_data["action"] = "add_receive_channel"

    async def remove_receive_channel_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """提示移除接收频道"""
        self.update_status("运行中 - 移除接收频道")
        channel_list = "\n".join([f"[{i}] {name}" for i, (_, name) in enumerate(self.receive_channels)])
        display_text = f"当前正在监控的信息频道为：\n{channel_list if channel_list else '无'}\n请选择要移除的监控频道编号"
        keyboard = []
        if self.receive_channels:
            buttons = [InlineKeyboardButton(f"{i}", callback_data=f"remove_ch_{i}") for i in range(min(3, len(self.receive_channels)))]
            keyboard.append(buttons)
        keyboard.append([InlineKeyboardButton("刷新", callback_data="remove_receive_channel")])
        keyboard.append([InlineKeyboardButton("返回", callback_data="query_receive_channel")])
        await self.delete_last_message(context, update.callback_query.message.chat_id)
        message = await update.callback_query.message.reply_text(display_text, reply_markup=InlineKeyboardMarkup(keyboard))
        self.last_message_id = message.message_id

    async def query_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """查询管理员"""
        self.update_status("运行中 - 查询管理员")
        admin_list = "\n".join([f"[{i}] {admin}" for i, admin in enumerate(self.admins)])
        display_text = f"当前管理员为：\n{admin_list}"
        keyboard = [
            [InlineKeyboardButton("增加管理员", callback_data="add_admin"),
             InlineKeyboardButton("移除管理员", callback_data="remove_admin")],
            [InlineKeyboardButton("刷新", callback_data="query_admin")],
            [InlineKeyboardButton("返回", callback_data="back")]
        ]
        await self.delete_last_message(context, update.callback_query.message.chat_id)
        message = await update.callback_query.message.reply_text(display_text, reply_markup=InlineKeyboardMarkup(keyboard))
        self.last_message_id = message.message_id

    async def add_admin_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """提示增加管理员"""
        self.update_status("运行中 - 增加管理员")
        admin_list = "\n".join([f"[{i}] {admin}" for i, admin in enumerate(self.admins)])
        display_text = f"当前管理员为：\n{admin_list}\n请输入新的管理员用户名（如 @username）"
        keyboard = [[InlineKeyboardButton("返回", callback_data="query_admin")]]
        await self.delete_last_message(context, update.callback_query.message.chat_id)
        message = await update.callback_query.message.reply_text(display_text, reply_markup=InlineKeyboardMarkup(keyboard))
        self.last_message_id = message.message_id
        context.user_data["action"] = "add_admin"

    async def remove_admin_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """提示移除管理员"""
        self.update_status("运行中 - 移除管理员")
        admin_list = "\n".join([f"[{i}] {admin}" for i, admin in enumerate(self.admins)])
        display_text = f"当前管理员为：\n{admin_list}\n请选择要移除的管理员编号"
        keyboard = []
        if self.admins:
            buttons = [InlineKeyboardButton(f"{i}", callback_data=f"remove_admin_{i}") for i in range(min(3, len(self.admins)))]
            keyboard.append(buttons)
        keyboard.append([InlineKeyboardButton("刷新", callback_data="remove_admin")])
        keyboard.append([InlineKeyboardButton("返回", callback_data="query_admin")])
        await self.delete_last_message(context, update.callback_query.message.chat_id)
        message = await update.callback_query.message.reply_text(display_text, reply_markup=InlineKeyboardMarkup(keyboard))
        self.last_message_id = message.message_id

    async def set_review_channel_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """提示设置审核频道"""
        self.update_status("运行中 - 设置审核频道")
        review_display = f"{self.review_channel[1]}({self.review_channel[0]})" if self.review_channel else "未设置"
        display_text = f"当前审核频道：{review_display}\n如需更改，请输入新审核频道 ID进行覆盖"
        keyboard = [[InlineKeyboardButton("返回", callback_data="back")]]
        await self.delete_last_message(context, update.callback_query.message.chat_id)
        message = await update.callback_query.message.reply_text(display_text, reply_markup=InlineKeyboardMarkup(keyboard))
        self.last_message_id = message.message_id
        context.user_data["action"] = "set_review_channel"

    async def set_publish_channel_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """提示设置发布频道"""
        self.update_status("运行中 - 设置发布频道")
        publish_display = f"{self.publish_channel[1]}({self.publish_channel[0]})" if self.publish_channel else "未设置"
        display_text = f"当前发布频道：{publish_display}\n如需更改，请输入新发布频道 ID进行覆盖"
        keyboard = [[InlineKeyboardButton("返回", callback_data="back")]]
        await self.delete_last_message(context, update.callback_query.message.chat_id)
        message = await update.callback_query.message.reply_text(display_text, reply_markup=InlineKeyboardMarkup(keyboard))
        self.last_message_id = message.message_id
        context.user_data["action"] = "set_publish_channel"

    async def set_cycle_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """提示设置总结周期"""
        self.update_status("运行中 - 设置周期")
        display_text = f"当前总结周期：{self.summary_cycle} 分钟\n请输入新的总结周期（分钟）"
        keyboard = [[InlineKeyboardButton("返回", callback_data="back")]]
        await self.delete_last_message(context, update.callback_query.message.chat_id)
        message = await update.callback_query.message.reply_text(display_text, reply_markup=InlineKeyboardMarkup(keyboard))
        self.last_message_id = message.message_id
        context.user_data["action"] = "set_cycle"

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理用户输入"""
        username = update.effective_user.username
        if not self.is_admin(username):
            await update.message.reply_text("无权限，仅限管理员访问")
            return
        action = context.user_data.get("action")
        text = update.message.text
        self.update_status(f"运行中 - 处理输入: {action}")

        if action == "add_receive_channel":
            try:
                chat_id = int(text)
                chat = await context.bot.get_chat(chat_id)
                channel_name = chat.title
                self.receive_channels.append((str(chat_id), channel_name))
                self.save_config("receive_channels", self.receive_channels)
                log_info(f"已添加接收频道: {chat_id} ({channel_name})")
                await update.message.reply_text(f"{channel_name}({chat_id}) 已纳入监控视野")
                await self.update_receive_channels(context.application)
                await self.query_receive_channel(update, context)  # 刷新
            except (ValueError, TelegramError) as e:
                await update.message.reply_text(f"无效的频道 ID 或获取名称失败: {str(e)}")
        elif action == "set_review_channel":
            try:
                chat_id = int(text)
                chat = await context.bot.get_chat(chat_id)
                self.review_channel = (str(chat_id), chat.title)
                self.save_config("review_channel", self.review_channel)
                await update.message.reply_text(f"审核频道设置为：{chat.title}({chat_id})")
                await self.start(update, context)
            except (ValueError, TelegramError) as e:
                await update.message.reply_text(f"无效的频道 ID 或获取名称失败: {str(e)}")
        elif action == "set_publish_channel":
            try:
                chat_id = int(text)
                chat = await context.bot.get_chat(chat_id)
                self.publish_channel = (str(chat_id), chat.title)
                self.save_config("publish_channel", self.publish_channel)
                await update.message.reply_text(f"发布频道设置为：{chat.title}({chat_id})")
                await self.start(update, context)
            except (ValueError, TelegramError) as e:
                await update.message.reply_text(f"无效的频道 ID 或获取名称失败: {str(e)}")
        elif action == "set_cycle":
            try:
                self.summary_cycle = int(text)
                self.save_config("summary_cycle", self.summary_cycle)
                context.job_queue.run_repeating(self.summarize_cycle, interval=self.summary_cycle * 60, first=0)
                await update.message.reply_text(f"总结周期设置为：{text} 分钟")
                await self.start(update, context)
            except ValueError:
                await update.message.reply_text("无效的周期值，请输入数字")
        elif action == "add_admin":
            if text.startswith("@"):
                self.admins.append(text)
                self.save_config("admins", self.admins)
                log_info(f"已添加管理员: {text}")
                await update.message.reply_text(f"已添加管理员: {text}")
                await self.query_admin(update, context)
            else:
                await update.message.reply_text("无效的用户名，请以 @ 开头")
        context.user_data["action"] = None

    async def receive_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """接收并存储消息"""
        chat_id = update.message.chat_id
        if str(chat_id) not in [cid for cid, _ in self.receive_channels]:
            return
        message = {
            "timestamp": get_timestamp(),
            "source": update.message.text.split("\n")[0],
            "content": "\n".join(update.message.text.split("\n")[1:-2]),
            "attachment_link": update.message.text.split("\n")[-2],
            "original_link": update.message.text.split("\n")[-1]
        }
        self.update_status(f"运行中 - 接收消息: {message['content'][:20]}...")
        append_to_mempool(message)
        log_info(f"消息已存储到 S3: {message['content'][:20]}...")
        await context.bot.send_message(chat_id, "已接收消息")

    async def summarize_cycle(self, context: ContextTypes.DEFAULT_TYPE):
        """周期性总结消息"""
        self.update_status("运行中 - 周期性总结")
        messages = self.get_new_messages()
        if not messages:
            log_info("无新消息")
            return
        summaries = analyze_messages(messages, self.last_position)
        self.last_position = get_timestamp()
        self.save_config("last_position", self.last_position)
        for summary in summaries:
            log_info(f"总结结果: {summary}")
            if self.review_enabled and self.review_channel:
                await self.send_review(context, summary)
            elif self.publish_channel:
                await context.bot.send_message(self.publish_channel[0], summary)
                save_published_message({"content": summary, "timestamp": get_timestamp()})
        log_info(f"总结完成，位置: {self.last_position}")

    def get_new_messages(self):
        """从 S3 获取新消息"""
        files = list_s3_files("intel_mempool", self.last_position)
        messages = []
        for timestamp, key in files:
            data = load_from_s3("intel_mempool", key.split("/")[-1])
            if data:
                messages.append(data)
        log_info(f"获取到 {len(messages)} 条新消息")
        return messages

    async def send_review(self, context, summary):
        """发送消息到审核频道"""
        key = f"approve_{summary[:10]}"
        context.bot_data[key] = summary
        keyboard = [
            [InlineKeyboardButton("通过", callback_data=key),
             InlineKeyboardButton("驳回", callback_data=f"reject_{summary[:10]}")]
        ]
        self.update_status(f"运行中 - 发送审核: {summary[:20]}...")
        await context.bot.send_message(self.review_channel[0], summary, reply_markup=InlineKeyboardMarkup(keyboard))


def main():
    """启动 Bot"""
    bot = CryptoBot()
    bot.update_status("Bot 启动")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("get_id", bot.get_id))
    application.add_handler(CommandHandler("summarize", bot.summarize))
    application.add_handler(CallbackQueryHandler(bot.handle_button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text))

    if bot.receive_channels:
        application.add_handler(
            MessageHandler(filters.Chat([int(cid) for cid, _ in bot.receive_channels]), bot.receive_message)
        )

    application.job_queue.run_repeating(
        bot.summarize_cycle,
        interval=bot.summary_cycle * 60,
        first=0
    )

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()