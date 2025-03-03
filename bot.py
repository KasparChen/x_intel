import json
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.error import TelegramError

# 假设这些模块存在，用于模拟完整功能
from config import TELEGRAM_TOKEN, ADMIN_HANDLES, DEFAULT_SUMMARY_CYCLE
from s3_storage import append_to_mempool, save_published_message, list_s3_files, load_from_s3
from llm_agent import analyze_messages
from utils import log_info, log_error, get_timestamp, format_summary

class CryptoBot:
    def __init__(self):
        """
        初始化 Bot 的状态和配置。
        """
        self.admins = ADMIN_HANDLES  # 从 config.py 加载管理员列表
        self.receive_channels = []  # 存储 (chat_id, channel_name) 元组，表示接收消息的频道
        self.review_channel = None  # 审核频道 ID
        self.publish_channel = None  # 发布频道 ID
        self.review_enabled = True  # 审核默认开启
        self.summary_cycle = DEFAULT_SUMMARY_CYCLE  # 总结周期（分钟）
        self.last_position = "2025-03-03 00:00:00"  # 最后分析的时间戳
        self.status = "初始化中"  # Bot 当前状态

    def is_admin(self, username):
        """检查用户是否为管理员"""
        return f"@{username}" in self.admins

    def update_status(self, status):
        """更新并记录 Bot 状态"""
        self.status = status
        log_info(f"Bot 状态更新: {status}")  # 记录到日志
        print(f"Bot 状态更新: {status}")  # 输出到命令行
        return f"当前 Bot 状态: {status}"

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """显示管理菜单（仅限管理员）"""
        username = update.effective_user.username
        if not self.is_admin(username):
            await update.message.reply_text("无权限，仅限管理员访问")
            return
        status_message = self.update_status("运行中 - 管理菜单已打开")
        # 主菜单按钮布局
        keyboard = [
            [InlineKeyboardButton("查询接收频道", callback_data="query_receive_channel")],  # 第一行
            [InlineKeyboardButton("开启审核 🟡" if self.review_enabled else "开启审核", callback_data="enable_review"),
             InlineKeyboardButton("关闭审核" if self.review_enabled else "关闭审核 🔵", callback_data="disable_review")],  # 第二行
            [InlineKeyboardButton("查询管理员", callback_data="query_admin")],  # 第三行
            [InlineKeyboardButton("设置审核频道", callback_data="set_review_channel"),
             InlineKeyboardButton("设置周期", callback_data="set_cycle")]  # 第四行
        ]
        await update.message.reply_text(status_message, reply_markup=InlineKeyboardMarkup(keyboard))

    async def get_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """获取当前群组或频道的 ID（无需管理员权限）"""
        chat_id = update.message.chat_id
        status_message = self.update_status(f"运行中 - 获取 ID: {chat_id}")
        try:
            chat = await context.bot.get_chat(chat_id)
            await update.message.reply_text(f"{status_message}\n当前群组/频道 ID: {chat_id} (名称: {chat.title})")
        except TelegramError as e:
            await update.message.reply_text(f"{status_message}\n当前群组/频道 ID: {chat_id} (无法获取名称: {str(e)})")

    async def summarize(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """主动触发消息总结（仅限管理员）"""
        username = update.effective_user.username
        if not self.is_admin(username):
            await update.message.reply_text("无权限，仅限管理员访问")
            return
        status_message = self.update_status("运行中 - 主动总结请求")
        keyboard = [
            [InlineKeyboardButton("不重置周期计时", callback_data="summarize_no_reset"),
             InlineKeyboardButton("重置周期计时", callback_data="summarize_reset")]
        ]
        await update.message.reply_text(f"{status_message}\n选择总结选项：", reply_markup=InlineKeyboardMarkup(keyboard))

    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理按钮点击（仅限管理员）"""
        query = update.callback_query
        await query.answer()
        data = query.data
        username = query.from_user.username
        if not self.is_admin(username):
            await query.edit_message_text("无权限，仅限管理员访问")
            return

        status_message = self.update_status(f"运行中 - 处理按钮: {data}")
        await query.edit_message_text(f"{status_message}\n处理中...")

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
                log_info(f"已移除接收频道: {chat_id} ({name})")  # 记录到日志
                print(f"已移除接收频道: {chat_id} ({name})")  # 输出到命令行
                await query.message.reply_text(f"{status_message}\n{chat_id}已被移除")
            await self.remove_receive_channel_prompt(update, context)  # 刷新移除界面
        elif data == "enable_review":
            self.review_enabled = True
            log_info("审核已开启")  # 记录到日志
            print("审核已开启")  # 输出到命令行
            await query.edit_message_text(f"{status_message}\n审核已开启")
            await self.start(update, context)  # 返回主菜单
        elif data == "disable_review":
            self.review_enabled = False
            log_info("审核已关闭")  # 记录到日志
            print("审核已关闭")  # 输出到命令行
            await query.edit_message_text(f"{status_message}\n审核已关闭")
            await self.start(update, context)  # 返回主菜单
        elif data == "query_admin":
            await query.edit_message_text(f"{status_message}\n当前管理员：{', '.join(self.admins)}",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("返回", callback_data="back")]]))
        elif data == "set_review_channel":
            await query.edit_message_text(f"{status_message}\n当前审核频道：{self.review_channel or '未设置'}\n请输入新审核频道 ID",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("返回", callback_data="back")]]))
            context.user_data["action"] = "set_review_channel"
        elif data == "set_cycle":
            await query.edit_message_text(f"{status_message}\n请输入总结周期（分钟）",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("返回", callback_data="back")]]))
            context.user_data["action"] = "set_cycle"
        elif data == "back":
            await self.start(update, context)

    async def query_receive_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """查询接收频道选项卡"""
        status_message = self.update_status("运行中 - 查询接收频道")
        channel_list = "\n".join([f"[{i}] {name}" for i, (_, name) in enumerate(self.receive_channels)])
        display_text = f"{status_message}\n当前正在监控的信息频道为：\n{channel_list if channel_list else '无'}"
        keyboard = [
            [InlineKeyboardButton("增加接收频道", callback_data="add_receive_channel"),
             InlineKeyboardButton("移除接收频道", callback_data="remove_receive_channel")],  # 第一行
            [InlineKeyboardButton("刷新", callback_data="query_receive_channel")],  # 第二行
            [InlineKeyboardButton("返回", callback_data="back")]  # 第三行
        ]
        await update.callback_query.edit_message_text(display_text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def add_receive_channel_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """提示用户输入新的频道 ID"""
        status_message = self.update_status("运行中 - 增加接收频道")
        channel_list = "\n".join([f"[{i}] {name}" for i, (_, name) in enumerate(self.receive_channels)])
        display_text = f"{status_message}\n当前正在监控的信息频道为：\n{channel_list if channel_list else '无'}\n请输入新的 channel ID 如: -184301982"
        keyboard = [
            [InlineKeyboardButton("返回", callback_data="query_receive_channel")]  # 第一行
        ]
        await update.callback_query.edit_message_text(display_text, reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data["action"] = "add_receive_channel"

    async def remove_receive_channel_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """提示用户选择要移除的频道"""
        status_message = self.update_status("运行中 - 移除接收频道")
        channel_list = "\n".join([f"[{i}] {name}" for i, (_, name) in enumerate(self.receive_channels)])
        display_text = f"{status_message}\n当前正在监控的信息频道为：\n{channel_list if channel_list else '无'}\n请选择要移除的监控频道编号"
        keyboard = []
        if self.receive_channels:
            # 第一行：显示最多 3 个编号按钮 + 翻页按钮
            buttons = [InlineKeyboardButton(f"{i}", callback_data=f"remove_ch_{i}") for i in range(min(3, len(self.receive_channels)))]
            if len(self.receive_channels) > 3:
                buttons.append(InlineKeyboardButton("下一页", callback_data="next_page"))
            keyboard.append(buttons)
        # 第二行：刷新按钮
        keyboard.append([InlineKeyboardButton("刷新", callback_data="remove_receive_channel")])
        # 第三行：返回按钮
        keyboard.append([InlineKeyboardButton("返回", callback_data="back")])
        await update.callback_query.edit_message_text(display_text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理用户输入（仅限管理员）"""
        username = update.effective_user.username
        if not self.is_admin(username):
            await update.message.reply_text("无权限，仅限管理员访问")
            return
        action = context.user_data.get("action")
        text = update.message.text
        status_message = self.update_status(f"运行中 - 处理文本输入: {action}")
        if action == "add_receive_channel":
            try:
                chat_id = int(text)
                chat = await context.bot.get_chat(chat_id)
                channel_name = chat.title
                self.receive_channels.append((str(chat_id), channel_name))
                log_info(f"已添加接收频道: {chat_id} ({channel_name})")  # 记录到日志
                print(f"已添加接收频道: {chat_id} ({channel_name})")  # 输出到命令行
                await update.message.reply_text(f"{status_message}\n正在监控{chat_id}")
            except (ValueError, TelegramError) as e:
                await update.message.reply_text(f"{status_message}\n无效的频道 ID 或获取名称失败: {str(e)}")
        elif action == "set_review_channel":
            self.review_channel = text
            log_info(f"审核频道设置为: {text}")  # 记录到日志
            print(f"审核频道设置为: {text}")  # 输出到命令行
            await update.message.reply_text(f"{status_message}\n审核频道设置为：{text}")
        elif action == "set_cycle":
            try:
                self.summary_cycle = int(text)
                log_info(f"总结周期设置为: {text} 分钟")  # 记录到日志
                print(f"总结周期设置为: {text} 分钟")  # 输出到命令行
                context.job_queue.run_repeating(self.summarize_cycle, interval=self.summary_cycle * 60, first=0)
                await update.message.reply_text(f"{status_message}\n总结周期设置为：{text} 分钟")
            except ValueError:
                await update.message.reply_text(f"{status_message}\n无效的周期值，请输入数字")
        context.user_data["action"] = None

    async def receive_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """接收消息并存储"""
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
        status_message = self.update_status(f"运行中 - 接收消息: {message['content'][:20]}...")
        append_to_mempool(message)
        await context.bot.send_message(chat_id, f"{status_message}\n已接收消息")

    async def summarize_cycle(self, context: ContextTypes.DEFAULT_TYPE):
        """周期性总结消息"""
        status_message = self.update_status("运行中 - 周期性总结")
        messages = self.get_new_messages()
        if not messages:
            log_info(f"{status_message}\n无新消息")
            return
        summaries = analyze_messages(messages, self.last_position)
        self.last_position = get_timestamp()
        for summary in summaries:
            log_info(f"周期性总结: {summary}")
            if self.review_enabled:
                await self.send_review(context, summary)
            else:
                await context.bot.send_message(self.publish_channel, summary)
                save_published_message({"content": summary, "timestamp": get_timestamp()})
        log_info(f"{status_message}\n总结完成，位置: {self.last_position}")

    def get_new_messages(self):
        """获取新消息"""
        files = list_s3_files("intel_mempool", self.last_position)
        messages = []
        for timestamp, key in files:
            data = load_from_s3("intel_mempool", key.split("/")[-1])
            if data:
                messages.append(data)
        return messages

    async def send_review(self, context, summary):
        """发送消息到审核频道"""
        key = f"approve_{summary[:10]}"
        context.bot_data[key] = summary
        keyboard = [
            [InlineKeyboardButton("通过", callback_data=key),
             InlineKeyboardButton("驳回", callback_data=f"reject_{summary[:10]}")]
        ]
        status_message = self.update_status(f"运行中 - 发送审核: {summary[:20]}...")
        await context.bot.send_message(self.review_channel, f"{status_message}\n{summary}", reply_markup=InlineKeyboardMarkup(keyboard))

def main():
    """主函数，启动 Bot"""
    bot = CryptoBot()
    status_message = bot.update_status("Bot 启动")
    print(status_message)
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.job_queue = application.job_queue or application.updater.job_queue
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("get_id", bot.get_id))
    application.add_handler(CommandHandler("summarize", bot.summarize))
    application.add_handler(CallbackQueryHandler(bot.handle_button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text))
    application.job_queue.run_repeating(bot.summarize_cycle, interval=bot.summary_cycle * 60, first=0)
    application.run_polling()

if __name__ == "__main__":
    main()