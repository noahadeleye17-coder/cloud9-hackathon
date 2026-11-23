"""Historical pro match data (from GRID, public data, or the synthetic demo seed)."""

from django.db import models

from . import draft_rules as rules

SIDE_CHOICES = [(rules.BLUE, "Blue"), (rules.RED, "Red")]
ACTION_CHOICES = [(rules.PICK, "Pick"), (rules.BAN, "Ban")]
ROLE_CHOICES = [(r, r.title()) for r in rules.ROLES]
FORMAT_CHOICES = [("bo1", "Bo1"), ("bo3", "Bo3"), ("bo5", "Bo5")]
FEARLESS_CHOICES = [
    (rules.FEARLESS_HARD, "Hard Fearless"),
    (rules.FEARLESS_SOFT, "Soft Fearless"),
    (rules.FEARLESS_OFF, "Normal draft"),
]


class DataSource(models.TextChoices):
    GRID = "grid", "GRID"
    PUBLIC = "public", "Public match data"
    SYNTHETIC = "synthetic", "Synthetic demo data"


class Team(models.Model):
    id: int  # auto primary key, declared for type checkers

    name = models.CharField(max_length=100)
    short_code = models.CharField(max_length=10, unique=True)
    region = models.CharField(max_length=30, blank=True)
    external_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.short_code})"


class Champion(models.Model):
    id: int  # auto primary key, declared for type checkers

    key = models.CharField(max_length=40, unique=True, help_text="Data Dragon id, e.g. 'MonkeyKing'")
    name = models.CharField(max_length=60)
    roles = models.JSONField(default=list, blank=True, help_text="Lanes, most common first")
    image_url = models.URLField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def primary_role(self):
        return self.roles[0] if self.roles else None


class Series(models.Model):
    id: int  # auto primary key, declared for type checkers

    team_a = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    team_b = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    format = models.CharField(max_length=3, choices=FORMAT_CHOICES)
    fearless_mode = models.CharField(max_length=4, choices=FEARLESS_CHOICES, default=rules.FEARLESS_HARD)
    date = models.DateField()
    patch = models.CharField(max_length=10, db_index=True)
    league = models.CharField(max_length=40, blank=True)
    source = models.CharField(max_length=10, choices=DataSource.choices, default=DataSource.SYNTHETIC)
    external_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ["-date"]
        verbose_name_plural = "series"

    def __str__(self):
        return f"{self.team_a.short_code} vs {self.team_b.short_code} {self.format} {self.date}"

    @property
    def is_fearless(self):
        return self.fearless_mode != rules.FEARLESS_OFF


class Game(models.Model):
    id: int  # auto primary key, declared for type checkers

    series = models.ForeignKey(Series, on_delete=models.CASCADE, related_name="games")
    number = models.PositiveSmallIntegerField()
    blue_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    red_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    winner = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    patch = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ["series", "number"]
        constraints = [models.UniqueConstraint(fields=["series", "number"], name="unique_game_number")]

    def __str__(self):
        return f"{self.series} G{self.number}"

    def team_on(self, side):
        return self.blue_team if side == rules.BLUE else self.red_team

    def side_of(self, team_id):
        if team_id == self.blue_team_id:
            return rules.BLUE
        if team_id == self.red_team_id:
            return rules.RED
        return None


class DraftAction(models.Model):
    id: int  # auto primary key, declared for type checkers

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="actions")
    order = models.PositiveSmallIntegerField(help_text="1..20, see draft_rules.DRAFT_ORDER")
    side = models.CharField(max_length=4, choices=SIDE_CHOICES)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="draft_actions")
    action_type = models.CharField(max_length=4, choices=ACTION_CHOICES)
    champion = models.ForeignKey(Champion, on_delete=models.CASCADE, related_name="draft_actions")
    role = models.CharField(max_length=8, choices=ROLE_CHOICES, blank=True, help_text="Picks only")

    class Meta:
        ordering = ["game", "order"]
        constraints = [models.UniqueConstraint(fields=["game", "order"], name="unique_draft_step")]
        indexes = [models.Index(fields=["team", "action_type"])]

    def __str__(self):
        return f"{self.game} #{self.order} {self.team.short_code} {self.action_type} {self.champion}"
