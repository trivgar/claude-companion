# Fitness sources

The Fitness tab shows sleep, resting heart rate, steps, distance and weight. It
does not read a device itself. It reads a **source**, picked in the config:

```toml
fitness_source = "home_assistant"   # or "demo"; empty means no fitness source set up
units = "imperial"                 # or "metric" (the default). Data is stored metric; this is only how the page says it.
fitness_sleep_goal_min = 420       # optional, minutes asleep per night
fitness_work_days = "mon,tue,wed,thu,fri"   # optional, see below
```

With `fitness_source` empty the tab says "No fitness source set up" and points
here. `fitness_source = "demo"` shows made-up data, marked DEMO, so you can see
the page first.

## The rules every source follows

- **A night belongs to the morning it ends.** The row dated Monday carries the
  sleep that ended Monday morning.
- **Not recorded is a fact, never zero.** A night with no value, a zero, or the
  same minutes as the morning before (the device did not move, so nothing new
  was posted) is left out of every average and of the sleep debt, and the page
  says how many nights were recorded. A source returns `None` for anything it
  does not have. It never invents a 0.
- **Work nights.** If you set `fitness_work_days`, the page splits time asleep
  into the mornings you get up for work and the rest. Leave it out and there is
  no split. The names are the mornings (`mon` means the night that ends Monday).

## Home Assistant

The Fitbit, Google Health, Withings and Garmin Connect integrations (and the
Companion app's step sensors) put these values into Home Assistant as sensors.
Home Assistant keeps their long-term statistics for good, which is what this
reads, over its websocket (`recorder/statistics_during_period`). Raw history
is purged after days, and the REST API does not serve statistics.

1. In Home Assistant, open your profile page, scroll to "Long-lived access
   tokens" and create one. Save it in a file only you can read:
   `install -m 600 /dev/null ~/.config/agent-companion/ha-token` then paste the
   token into it. The config holds the file's path, never the token.
2. Find the entity ids: Settings, Devices and services, Entities, search for
   "sleep", "heart", "steps", "distance", "weight". The id is the
   `sensor.something` column. Developer tools, Statistics, lists the sensors
   that have long-term statistics; a sensor missing from that list will not
   work here.
3. Put it in the config. Every entity is optional; leave out what you do not track.

```toml
fitness_source = "home_assistant"
ha_url = "http://ha.example.com:8123"
ha_token_file = "~/.config/agent-companion/ha-token"
fitness_sleep_entity = "sensor.example_sleep_minutes_asleep"
fitness_hr_entity = "sensor.example_resting_heart_rate"
fitness_steps_entity = "sensor.example_daily_steps"
fitness_distance_entity = "sensor.example_daily_distance"
fitness_weight_entity = "sensor.example_weight"
```

Units of the sensors, when they are not minutes, kilometres and kilograms:

```toml
fitness_sleep_unit = "h"        # min (default) or h
fitness_distance_unit = "mi"    # km (default), mi, m or ft
fitness_weight_unit = "lb"      # kg (default) or lb
fitness_read_hour = 20          # see below
```

Sleep, steps and distance are read as the day's last value. Heart rate and
weight are read as the mean of the hour starting at `fitness_read_hour` (20 by
default), after the device's morning post and before the next night: a daily
mean would mix in the day before.

It needs `pip install -e '.[home_assistant]'` (the `websockets` package). Nothing
is read unless `fitness_source = "home_assistant"`, and the server asks at most
once every ten minutes.

## Writing a direct source

Anything that can list one row per day can be a source: a Google Health
Connect export, a Garmin Connect download, Apple Health's `export.xml`. Ask a
Claude to write it. Give it this file and `agent_companion/fitness/__init__.py`.
A source is a class with `name`, `demo = False`, and two methods:

```python
class FitnessSource(Protocol):
    def daily(self, days: int) -> list[dict]:
        """The last `days` days up to today, oldest first. Each row:
        {date: 'YYYY-MM-DD', sleep_min, resting_hr, steps, distance_km, weight_kg}
        Metric. A value you do not have is None, never 0."""

    def freshness(self) -> dict:
        """{source: 'Garmin export', as_of: 'YYYY-MM-DD' or None}"""
```

Save it as `agent_companion/fitness/<name>.py` and register it at the bottom
of `agent_companion/fitness/__init__.py`:

```python
register("garmin", lambda: GarminSource(...))
```

Then `fitness_source = "garmin"`. Points to get right, each of which has cost
someone a wrong chart:

- **Dates are the morning a night ends.** Garmin and Health Connect both date a
  sleep session by when it started or by when it ended; check which and shift
  it. Apple Health's sleep records have a start and an end per stage: sum the
  asleep stages (not "in bed" or "awake") of the session that ends on the date.
- **Do not fill gaps.** If the export has no sleep for a date, return `None`
  for it (or leave the date out; the page treats a missing date as not
  recorded).
- **Metric in, always.** Minutes, kilometres, kilograms. The `units` key only
  changes what the page prints.
- **Read files, not secrets.** An export lives in a file you point at with a
  config key. If a service needs a login, keep the credential in a 600 file and
  put its path in the config, as `ha_token_file` does.
- **Never open a network connection under pytest.** The tests inject a fake
  transport instead; do the same, so the suite stays offline.

Add a test beside `tests/test_fitness.py` that feeds your parser a small canned
file and checks the rows, including a night with nothing in it.
