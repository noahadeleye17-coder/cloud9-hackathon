"""A coach's planned Fearless series: games, sides, notes and planned picks/bans."""

from django.db import models

from esports import draft_rules as rules
from esports.models import FEARLESS_CHOICES, FORMAT_CHOICES, ROLE_CHOICES, SIDE_CHOICES, Champion, Team


class DraftPlan(models.Model):
    id: int  # auto primary key, declared for type checkers

    name = models.CharField(max_length=120)
    our_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="plans_as_us")
    opponent = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="plans_as_opponent")
    format = models.CharField(max_length=3, choices=FORMAT_CHOICES, default="bo5")
    fearless_mode = models.CharField(max_length=4, choices=FEARLESS_CHOICES, default=rules.FEARLESS_HARD)
    scout_patch = models.CharField(max_length=10, blank=True, help_text="Limit suggestions to one patch (blank = all)")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        creating = self.pk is None
        super().save(*args, **kwargs)
        if creating:
            self.ensure_games()

    def ensure_games(self):
        """Create one PlannedGame per possible game. Sides alternate by default (coach can change)."""
        existing = set(self.games.values_list("number", flat=True))
        for n in range(1, rules.series_length(self.format) + 1):
            if n not in existing:
                PlannedGame.objects.create(plan=self, number=n, our_side=rules.BLUE if n % 2 else rules.RED)


class PlannedGame(models.Model):
    id: int  # auto primary key, declared for type checkers

    RESULT_CHOICES = [("", "Not played"), ("win", "Win"), ("loss", "Loss")]

    plan = models.ForeignKey(DraftPlan, on_delete=models.CASCADE, related_name="games")
    number = models.PositiveSmallIntegerField()
    our_side = models.CharField(max_length=4, choices=SIDE_CHOICES, default=rules.BLUE)
    result = models.CharField(max_length=4, choices=RESULT_CHOICES, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["plan", "number"]
        constraints = [models.UniqueConstraint(fields=["plan", "number"], name="unique_planned_game")]

    def __str__(self):
        return f"{self.plan} G{self.number}"

    def team_on(self, side):
        return self.plan.our_team if side == self.our_side else self.plan.opponent

    def team_id_on(self, side):
        return self.plan.our_team_id if side == self.our_side else self.plan.opponent_id

    @property
    def blue_team(self):
        return self.team_on(rules.BLUE)

    @property
    def red_team(self):
        return self.team_on(rules.RED)


class PlannedAction(models.Model):
    id: int  # auto primary key, declared for type checkers

    game = models.ForeignKey(PlannedGame, on_delete=models.CASCADE, related_name="actions")
    order = models.PositiveSmallIntegerField()
    champion = models.ForeignKey(Champion, on_delete=models.CASCADE, related_name="+")
    role = models.CharField(max_length=8, choices=ROLE_CHOICES, blank=True)

    class Meta:
        ordering = ["game", "order"]
        constraints = [models.UniqueConstraint(fields=["game", "order"], name="unique_planned_step")]

    def __str__(self):
        return f"{self.game} #{self.order} {self.champion}"

    @property
    def step(self):
        return rules.step(self.order)

    @property
    def team_id(self):
        return self.game.team_id_on(self.step.side)
