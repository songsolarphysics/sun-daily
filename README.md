# sun-daily

**[English]** | [中文](#中文)

One solar observation clip a day. Every night a GitHub Actions job draws a data source from a
pool, picks an event or observation from a catalogue, downloads the science files, renders a
short animation and publishes it with a card describing what it shows.

Gallery: <https://songsolarphysics.github.io/sun-daily/>

## How a night goes

1. **Pick.** A weighted draw over `config/pool.yaml`, damped for sources shown in the last few
   days. A request in `config/requests.yaml` goes first.
2. **Propose.** The source turns the draw into a ticket: flares come from the HEK, Solar Orbiter
   EUI/HRI sequences from an index built from the Solar Orbiter Archive file list.
3. **Fetch.** Science-level FITS only, within a nightly byte budget. A ticket that fails falls
   back to the next candidate, then to the next source.
4. **Process.** Exposure normalisation, one scaling for the whole sequence, and for HRI a
   de-jitter with [solar_align](https://github.com/songsolarphysics/solar_align-A-python-tool-for-solar-image-de-jittering).
5. **Render.** MP4 and poster. The frame carries a stamp, a scale bar and one tag, nothing else.
6. **Publish.** Clip, poster, card and ticket go to the `gh-pages` branch; the log goes to
   `ci-logs`.

## Sources

| key | what | access |
|---|---|---|
| `aia_closeup` | SDO/AIA full-resolution tracked cut-out around a flare, 12–24 s cadence | JSOC export, needs the `JSOC_EMAIL` secret |
| `aia_synoptic` | SDO/AIA whole disk for three hours around a large flare, 2 min cadence | public, no account |
| `eui_hri` | Solar Orbiter EUI/HRI<sub>EUV</sub> 174 Å high-cadence sequence | Solar Orbiter Archive, no account |

A new instrument is one subclass of `sundaily.sources.base.Source` (`propose`, `fetch`, `load`)
plus one line in `config/pool.yaml`.

## Use it

```bash
# request a specific clip tonight: add to config/requests.yaml, or run the workflow by hand
#   Actions > nightly > Run workflow   (source, event time, date are optional)

# get the science files behind a published clip
pip install git+https://github.com/songsolarphysics/sun-daily
sundaily fetch 2026-11-22            # into ./sun-daily-data/2026-11-22/

# offline test (synthetic data, no network)
python tests/test_offline.py
```

Repository settings this needs: a `JSOC_EMAIL` Actions secret (an e-mail registered with the
JSOC export system) for `aia_closeup`, and GitHub Pages serving the `gh-pages` branch.

## 中文

每天一段太阳观测动画。GitHub Actions 每晚从源池里抽一个数据源，从事件或观测目录里选题，下载科学级
FITS，渲染成短动画，连同一张说明卡片发布到图库。

- 源池与权重：`config/pool.yaml`；点播：`config/requests.yaml`；体量、时长等默认值：`config/settings.yaml`。
- 手动跑一次：Actions → nightly → Run workflow，可指定数据源和事件时间。
- 想拿某一天动画背后的原始数据：`sundaily fetch 2026-11-22`。
- 加新仪器：在 `sundaily/sources/` 下写一个 `Source` 子类（`propose`、`fetch`、`load`），再在源池里加一行。

Licence: MIT. `sundaily/vendor/solar_align.py` is an unmodified copy of solar_align (MIT).
