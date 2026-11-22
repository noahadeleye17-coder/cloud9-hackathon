from django.core.management import call_command
from django.core.management.base import BaseCommand

from esports.models import Champion, DataSource, Series
from esports.synthetic import Generator


class Command(BaseCommand):
    help = "Generate SYNTHETIC demo series (tagged source=synthetic) so the app is usable before GRID data."

    def add_arguments(self, parser):
        parser.add_argument("--series", type=int, default=70)
        parser.add_argument("--seed", type=int, default=9)
        parser.add_argument("--reset", action="store_true", help="Delete existing synthetic series first")
        parser.add_argument("--if-empty", action="store_true", help="Do nothing if any series exist (for deploys)")

    def handle(self, *args, series, seed, reset, if_empty, **opts):
        if if_empty and Series.objects.exists():
            self.stdout.write("Series already exist; skipping demo seed.")
            return
        if not Champion.objects.exists():
            call_command("load_champions", stdout=self.stdout)
        if reset:
            n, _ = Series.objects.filter(source=DataSource.SYNTHETIC).delete()
            self.stdout.write(f"Deleted {n} synthetic rows.")
        result = Generator(seed=seed).generate(n_series=series)
        self.stdout.write(self.style.SUCCESS(f"Synthetic data: {result}"))
