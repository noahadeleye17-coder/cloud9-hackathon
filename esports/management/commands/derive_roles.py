from collections import Counter, defaultdict

from django.core.management.base import BaseCommand

from esports.models import Champion, DataSource, DraftAction


class Command(BaseCommand):
    help = "Set each champion's roles from how they were actually played in imported pro games."

    def add_arguments(self, parser):
        parser.add_argument("--min-share", type=float, default=0.15, help="Min share of picks for a role to count")
        parser.add_argument(
            "--include-synthetic", action="store_true", help="Also learn from synthetic demo data (off by default)"
        )

    def handle(self, *args, min_share, include_synthetic, **opts):
        qs = DraftAction.objects.filter(action_type="pick").exclude(role="")
        if not include_synthetic:
            qs = qs.exclude(game__series__source=DataSource.SYNTHETIC)
        counts = defaultdict(Counter)
        for champ_id, role in qs.values_list("champion_id", "role"):
            counts[champ_id][role] += 1

        changed = 0
        for champ in Champion.objects.filter(id__in=counts):
            c = counts[champ.id]
            total = sum(c.values())
            roles = [r for r, n in c.most_common() if n / total >= min_share]
            if roles and roles != champ.roles:
                champ.roles = roles
                champ.save(update_fields=["roles"])
                changed += 1
        self.stdout.write(self.style.SUCCESS(f"Updated roles for {changed} champions from {qs.count()} picks."))
