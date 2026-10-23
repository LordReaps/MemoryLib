from django.core.management.base import BaseCommand

from places.telegram_bot import start_telegram_bot


class Command(BaseCommand):
    help = "Run the approval Telegram bot for queued change requests."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Запускаю Telegram-бота..."))
        start_telegram_bot()
