import json
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from config import TELEGRAM_TOKEN, ADMIN_HANDLES, DEFAULT_SUMMARY_CYCLE
from s3_storage import append_to_mempool, save_published_message, list_s3_files, load_from_s3
from llm_agent import analyze_messages
from utils import log_info, log_error, get_timestamp, format_summary

class CryptoBot:
    def __init__(self):
        self.admins = ADMIN_HANDLES
        self.receive_channels = []
        self.review_channel = None
        self.publish_channel = None
        self.review_enabled = False
        self.summary_cycle = DEFAULT_SUMMARY_CYCLE
        self.last_position = "2025-03-03 00:00:00"  # 初始化位置
        self.status = "初始化中"

    def is_admin(self, username):
        """检查用户是否为管理员"""
        return f"@{username}" in self.admins

    def update_status(self, status):
        """更新并记录 Bot 状态"""
        self.status = status
        log_info(f"Bot 状态更新: {status}")
        return f"当前 Bot 状态: {status}"

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """显示管理菜单（仅限管理员）"""
        username = update.effective_user.username
        if not self.is_admin(username):
            await update.message.reply_text("无权限，仅限管理员访问")
            return
        status_message = self.update_status("运行中 - 管理菜单已打开")
        await update.message.reply_text(status_message)
        keyboard = [
            [InlineKeyboardButton("增加管理员", callback_data="add_admin")],
            [InlineKeyboardButton("移除管理员", callback_data="remove_admin")],
            [InlineKeyboardButton("查询管理员", callback_data="query_admin")],
            [InlineKeyboardButton("添加信息接收频道", callback_data="add_receive_channel")],
            [InlineKeyboardButton("查询接收频道", callback_data="query_receive_channel")],
            [InlineKeyboardButton("移除接收频道", callback_data="remove_receive_channel")],
            [InlineKeyboardButton("设置审核频道", callback_data="set_review_channel")],
            [InlineKeyboardButton("设置发布频道", callback_data="set_publish_channel")],
            [InlineKeyboardButton("开启审核", callback_data="enable_review")],
            [InlineKeyboardButton("关闭审核", callback_data="disable_review")],
            [InlineKeyboardButton("配置总结周期", callback_data="set_cycle")]
        ]
        await update.message.reply_text("管理菜单：", reply_markup=InlineKeyboardMarkup(keyboard))

    async def get_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """获取当前群组或频道的 ID（无需管理员权限）"""
        chat_id = update.message.chat_id
        status_message = self.update_status(f"运行中 - 获取 ID: {chat_id}")
        await update.message.reply_text(f"{status_message}\n当前群组/频道 ID: {chat_id}")

    async def summarize(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """主动触发消息总结（仅限管理员）"""
        username = update.effective_user.username
        if not self.is_admin(username):
            await update.message.reply_text("无权限，仅限管理员访问")
            return
        status_message = self.update_status("运行中 - 主动总结请求")
        await update.message.reply_text(status_message)
        keyboard = [
            [InlineKeyboardButton("不重置周期计时", callback_data="summarize_no_reset"),
             InlineKeyboardButton("重置周期计时", callback_data="summarize_reset")]
        ]
        await update.message.reply_text("选择总结选项：", reply_markup=InlineKeyboardMarkup(keyboard))

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

        if data == "add_admin":
            await query.edit_message_text(f"{status_message}\n当前管理员：{', '.join(self.admins)}\n请输入要添加的管理员handle_name, eg @kaspar", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("返回", callback_data="back")]]))
            context.user_data["action"] = "add_admin"
        elif data == "remove_admin":
            await query.edit_message_text(f"{status_message}\n当前管理员：{', '.join(self.admins)}\n请输入要移除的管理员handle_name, eg @kaspar", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("返回", callback_data="back")]]))
            context.user_data["action"] = "remove_admin"
        elif data == "query_admin":
            await query.edit_message_text(f"{status_message}\n当前管理员：{', '.join(self.admins)}")
        elif data == "add_receive_channel":
            await query.edit_message_text(f"{status_message}\n请输入频道ID, eg -100123456789", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("返回", callback_data="back")]]))
            context.user_data["action"] = "add_receive_channel"
        elif data == "query_receive_channel":
            await query.edit_message_text(f"{status_message}\n当前接收频道：{', '.join(self.receive_channels)}")
        elif data == "remove_receive_channel":
            keyboard = [[InlineKeyboardButton(f"{i}: {ch}", callback_data=f"remove_ch_{i}") for i, ch in enumerate(self.receive_channels)]]
            await query.edit_message_text(f"{status_message}\n当前接收频道：\n{chr(10).join([f'{i}: {ch}' for i, ch in enumerate(self.receive_channels)])}", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data.startswith("remove_ch_"):
            idx = int(data.split("_")[2])
            await query.edit_message_text(f"{status_message}\n确认移除 {self.receive_channels[idx]}?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("确认", callback_data=f"confirm_remove_ch_{idx}")]]))
        elif data.startswith("confirm_remove_ch_"):
            idx = int(data.split("_")[3])
            self.receive_channels.pop(idx)
            await query.edit_message_text(f"{status_message}\n移除成功")
        elif data == "set_review_channel":
            await query.edit_message_text(f"{status_message}\n当前审核频道：{self.review_channel or '未设置'}\n请输入新审核频道ID", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("返回", callback_data="back")]]))
            context.user_data["action"] = "set_review_channel"
        elif data == "set_publish_channel":
            await query.edit_message_text(f"{status_message}\n当前发布频道：{self.publish_channel or '未设置'}\n请输入新发布频道ID", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("返回", callback_data="back")]]))
            context.user_data["action"] = "set_publish_channel"
        elif data == "enable_review":
            self.review_enabled = True
            await query.edit_message_text(f"{status_message}\n审核已开启")
        elif data == "disable_review":
            self.review_enabled = False
            await query.edit_message_text(f"{status_message}\n审核已关闭")
        elif data == "set_cycle":
            await query.edit_message_text(f"{status_message}\n请输入总结周期（分钟）", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("返回", callback_data="back")]]))
            context.user_data["action"] = "set_cycle"
        elif data.startswith("approve_"):
            summary = context.bot_data.get(data, "")
            await query.edit_message_text(f"{status_message}\n确认通过?\n{summary}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("确认", callback_data=f"confirm_approve_{data}")]]))
        elif data.startswith("reject_"):
            summary = context.bot_data.get(data.replace("reject_", "approve_"), "")
            await query.edit_message_text(f"{status_message}\n确认驳回?\n{summary}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("确认", callback_data=f"confirm_reject_{data}")]]))
        elif data.startswith("confirm_approve_"):
            summary = context.bot_data.pop(data.replace("confirm_approve_", "approve_"), "")
            await context.bot.send_message(self.publish_channel, summary)
            save_published_message({"content": summary, "timestamp": get_timestamp()})
            await query.edit_message_text(f"{status_message}\n已发布")
        elif data.startswith("confirm_reject_"):
            context.bot_data.pop(data.replace("confirm_reject_", "approve_"), "")
            await query.edit_message_text(f"{status_message}\n已驳回")
        elif data == "summarize_no_reset":
            await self.perform_summarize(update, context, reset_cycle=False)
        elif data == "summarize_reset":
            await self.perform_summarize(update, context, reset_cycle=True)
        elif data == "back":
            await self.start(update, context)

    async def perform_summarize(self, update, context, reset_cycle):
        """执行消息总结逻辑"""
        status_message = self.update_status("运行中 - 执行总结")
        await update.message.reply_text(status_message)
        messages = self.get_new_messages()
        if not messages:
            await update.message.reply_text(f"{status_message}\n无新消息")
            return
        summaries = analyze_messages(messages, self.last_position)
        self.last_position = get_timestamp()
        for summary in summaries:
            log_info(f"处理总结: {summary}")
            if self.review_enabled:
                await self.send_review(context, summary)
            else:
                await context.bot.send_message(self.publish_channel, summary)
                save_published_message({"content": summary, "timestamp": get_timestamp()})
        if reset_cycle:
            self.summary_cycle = DEFAULT_SUMMARY_CYCLE
            context.job_queue.run_repeating(self.summarize_cycle, interval=self.summary_cycle * 60, first=0)
        await update.message.reply_text(f"{status_message}\n总结完成")

    async def receive_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.message.chat_id) not in self.receive_channels:
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
        await context.bot.send_message(update.message.chat_id, f"{status_message}\n已接收消息")

    async def summarize_cycle(self, context: ContextTypes.DEFAULT_TYPE):
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
        files = list_s3_files("intel_mempool", self.last_position)
        messages = []
        for timestamp, key in files:
            data = load_from_s3("intel_mempool", key.split("/")[-1])
            if data:
                messages.append(data)
        return messages

    async def send_review(self, context, summary):
        key = f"approve_{summary[:10]}"
        context.bot_data[key] = summary
        keyboard = [
            [InlineKeyboardButton("通过", callback_data=key), InlineKeyboardButton("驳回", callback_data=f"reject_{summary[:10]}")]
        ]
        status_message = self.update_status(f"运行中 - 发送审核: {summary[:20]}...")
        await context.bot.send_message(self.review_channel, f"{status_message}\n{summary}", reply_markup=InlineKeyboardMarkup(keyboard))

def main():
    bot = CryptoBot()
    status_message = bot.update_status("Bot 启动")
    print(status_message)  # 控制台输出
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("get_id", bot.get_id))
    application.add_handler(CommandHandler("summarize", bot.summarize))
    application.add_handler(CallbackQueryHandler(bot.handle_button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_message))
    application.job_queue.run_repeating(bot.summarize_cycle, interval=bot.summary_cycle * 60, first=0)
    application.run_polling()

if __name__ == "__main__":
    main()