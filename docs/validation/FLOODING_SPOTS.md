# Flooding Spots validation (MCGM layer 344) — pilot Hindmata / Dadar pilot

Generated 2026-09-09 00:09 on commit bf92843; horizon 180 min; blockage none. Measurement only — the model was not tuned for this check.

## What this is and is not

* MCGM "Flooding Spots" is a chronic, **undated** inventory (~2017); it is not a flood map for any storm. Only spots not flagged `(Delete)`/`(Tackled)` are scored (**active**); inactive ones are listed for reference.
* Footprint = MCGM polygon (cells whose centre falls inside) or, if absent, a 50 m radius around the centroid. The polygons in this snapshot are ~100-500 m circles, so 'max depth in footprint' is a generous test.
* Class: DETECTED >= 15 cm, MARGINAL 5-15 cm, MISSED < 5 cm (max modelled surface depth in footprint over the run). Working thresholds, not cited guidance.
* MCGM `DEPTH` / `STRETCH` attributes are free text of **unknown units**; they are reproduced verbatim, not compared.
* Base rate: if a large share of all pilot cells exceeds the detection depth, agreement is **uninformative** (anything would be 'detected'). The percentile column places each spot in the distribution of per-cell max depth.

## Scenario `heavy` — Heavy steady rain (50 mm/h x 2 h)

Active spots: **DETECTED 3 / MARGINAL 0 / MISSED 2** of 5. Mass-balance error -0.00 %.

Base rate: 20.0 % of 48900 non-building cells reach >= 15 cm at some point (peak single frame 18.7 % at t = 125 min); 35.2 % reach >= 5 cm. Per-cell max-depth percentiles (cm): p50=1.4, p75=11.3, p90=25.7, p95=36.0, p99=63.9.
The scoring depth is exceeded in a minority of cells, so DETECTED is informative here.

| Spot | Active | Footprint (cells) | Max depth cm (t) | Onset 5/15/30 cm (min) | Cell pctl | Class | Nearest node (dist m; surcharge) | Nearest road (dist m; max cm) | MCGM DEPTH/STRETCH (units?) | z min / relief m |
|---|---|---|---|---|---|---|---|---|---|---|
| Parel Station East J B Road, Elphinstone East/W | yes | polygon (721) | 245.6 (180) | 5/15/15 | 100 | **DETECTED** | 2172030109 (311; no) | Balasheth Mandurkar Marg (202; 0.2) | 2.0 / 40.0 | 16.14 / - |
| Dadar TT Near BEST Office Tilak road junction | yes | polygon (18) | 36.7 (140) | 15/60/100 | 95 | **DETECTED** | 2173045105 (8; no) | Jam-e-Jamshed Lane (2; 47.4) | 2.0 / 100.0 | 28.91 / -0.29 |
| Matunga Yard Road (Delete) | no | polygon (4) | 6.1 (115) | 40/-/- | 67 | **MARGINAL** | 2173044601 (27; yes @85 min, downstream) | Khareghat Road (1; 31.5) | 3.0 / 50.0 | 29.01 / 0.19 |
| Devdhar Road  (Tackled) | no | polygon (3) | 0.3 (120) | -/-/- | 21 | **MISSED** | 2173049905 (5; no) | Deodhar Road (0; 5.1) | 3.0 / 70.0 | 29.89 / -0.31 |
| 803 Laxumi Buvan (Delete) | no | polygon (13) | 54.8 (180) | 15/45/85 | 98 | **DETECTED** | 2173046301 (19; no) | (unnamed) (25; 54.3) | 2.0 / 70.0 | 28.64 / -0.52 |
| opp kalpataru lokmanya tilak Coloney (Delete) | no | polygon (9) | 39.6 (120) | 10/20/65 | 96 | **DETECTED** | 2173042202 (13; no) | Marg No 2 (0; 39.6) | 2.0 / 50.0 | 28.42 / -0.44 |
| 83 Hindu Coloney Dadar (Delete) | no | polygon (3) | 40.4 (150) | 25/60/105 | 96 | **DETECTED** | 2173042402 (12; no) | D.V. Pradhan Road (6; 41.1) | 3.0 / 200.0 | 28.20 / -0.41 |
| kabutar khana central matungaa near bharat bank (Delete) | no | polygon (4) | 72.1 (120) | 30/40/55 | 99 | **DETECTED** | 2173058306 (10; no) | Laxmi Narayan Lane (6; 72.1) | 2.0 / 200.0 | 28.60 / -0.57 |
| Ruiya Collage  (Tackled) | no | polygon (3) | 9.7 (120) | 25/-/- | 73 | **MARGINAL** | 2173047802 (68; no) | Lakhamasi Nappu Road (0; 55.2) | 3.0 / 100.0 | 28.80 / -0.16 |
| 89 Vaccharaj Lane | yes | polygon (2468) | 72.1 (120) | 10/15/40 | 99 | **DETECTED** | 2173059104 (8; no) | Chandavarkar Marg (2; 28.5) | 3.0 / 50.0 | 28.53 / -1.13 |
| Indian Bank Matunga Bazar Barnch (Delete) | no | polygon (4) | 5.8 (120) | 55/-/- | 66 | **MARGINAL** | 2173058004 (63; no) | Deodhar Road (5; 12.7) | 2.0 / 60.0 | 29.00 / -0.11 |
| Macherji Joshi Udyan (Delete) | no | polygon (10) | 1.1 (120) | -/-/- | 45 | **MISSED** | 2174041305 (4; no) | Lady Jehangir Road (1; 1.1) | 2.0 / 100.0 | 33.19 / -0.03 |
| Shindewadi Bus Stop (Tackled) | no | polygon (3) | 0.7 (65) | -/-/- | 37 | **MISSED** | 2172039502 (16; no) | Shankar Abaji Palav Marg (4; 29.2) | 1.5 / 100.0 | 28.39 / -0.27 |
| Senapati Bapat Roda (Delete) | no | polygon (16) | 53.7 (175) | 10/20/70 | 98 | **DETECTED** | 2172048202 (29; no) | (unnamed) (0; 41.8) | 2.0 / 100.0 | 28.00 / -0.79 |
| Shivaji Park Road No 2 (Delete) | no | polygon (4) | 24.3 (155) | 50/80/- | 89 | **DETECTED** | 2172057307 (34; no) | Lady Jamshedji Road (8; 34.5) | 2.0 / 100.0 | 29.60 / -0.16 |
| Ganesh Paethlem Shah Sadan Star Mall (Delete) | no | polygon (4) | 15.1 (130) | 50/125/- | 80 | **DETECTED** | 2172046803! (208; no) | Ganesh Peth Lane (3; 15.7) | 1.5 / 100.0 | 30.20 / -0.02 |
| Manish Market Beside Kohinoor Institute (Delete) | no | polygon (12) | 12.7 (120) | 35/-/- | 77 | **MARGINAL** | 2172047101 (26; no) | Senapati Bapat Marg (3; 30.3) | 2.0 / 50.0 | 28.24 / -0.48 |
| D L Vaidya Marg (Tackled) | no | polygon (5) | 16.6 (120) | 20/75/- | 82 | **DETECTED** | 2172046806 (53; no) | D L Vaidya Marg (3; 16.6) | None / None | 30.20 / 0.00 |
| Vijay Manjrekar Road (Tackled) | no | polygon (4) | 1.1 (120) | -/-/- | 46 | **MISSED** | 2172042403 (96; no) | (unnamed) (6; 2.0) | None / None | 30.20 / 0.40 |
| Shakkar panchyayat R.A.Kidwai | yes | polygon (7) | 3.0 (120) | -/-/- | 60 | **MISSED** | 2174031409 (28; no) | R.A. Kidwai Marg (3; 16.8) | None / None | 29.96 / -0.04 |
| Dadar Railway Station | yes | polygon (19) | 1.0 (120) | -/-/- | 44 | **MISSED** | 2173040101 (41; no) | Dadasaheb Phalke Marg (28; 11.7) | None / None | 28.94 / -0.06 |

### Diagnosis of active spots not DETECTED

**Shakkar panchyayat R.A.Kidwai** (MISSED, 3.0 cm): a drainage node (2174031409) sits 28 m away; it never surcharges in this run, i.e. the model thinks the local pipes cope; local relief is only -0.04 m, a very shallow depression at 10 m resolution; the nearest OSM segment (R.A. Kidwai Marg, 3 m) reaches at most 16.8 cm.

**Dadar Railway Station** (MISSED, 1.0 cm): a drainage node (2173040101) sits 41 m away; it never surcharges in this run, i.e. the model thinks the local pipes cope; local relief is only -0.06 m, a very shallow depression at 10 m resolution; the location is at/under a railway station or subway, a feature (underpass, track-side low) that a 10 m DTM interpolated from 20 cm contours does not resolve; the nearest OSM segment (Dadasaheb Phalke Marg, 28 m) reaches at most 11.7 cm.

## Scenario `july2005` — 26 July 2005 replay (Santacruz gauge, hourly)

Active spots: **DETECTED 5 / MARGINAL 0 / MISSED 0** of 5. Mass-balance error 0.00 %.

Base rate: 60.0 % of 48900 non-building cells reach >= 15 cm at some point (peak single frame 57.0 % at t = 180 min); 70.9 % reach >= 5 cm. Per-cell max-depth percentiles (cm): p50=26.1, p75=54.6, p90=77.8, p95=95.9, p99=162.5.
**Agreement is uninformative for this scenario: the model floods most of the pilot at the scoring depth.**

| Spot | Active | Footprint (cells) | Max depth cm (t) | Onset 5/15/30 cm (min) | Cell pctl | Class | Nearest node (dist m; surcharge) | Nearest road (dist m; max cm) | MCGM DEPTH/STRETCH (units?) | z min / relief m |
|---|---|---|---|---|---|---|---|---|---|---|
| Parel Station East J B Road, Elphinstone East/W | yes | polygon (721) | 689.3 (180) | 5/10/10 | 100 | **DETECTED** | 2172030109 (311; no) | Balasheth Mandurkar Marg (202; 0.4) | 2.0 / 40.0 | 16.14 / - |
| Dadar TT Near BEST Office Tilak road junction | yes | polygon (18) | 100.4 (180) | 10/30/50 | 96 | **DETECTED** | 2173045105 (8; yes @35 min, downstream) | Jam-e-Jamshed Lane (2; 115.2) | 2.0 / 100.0 | 28.91 / -0.29 |
| Matunga Yard Road (Delete) | no | polygon (4) | 23.9 (180) | 20/150/- | 48 | **DETECTED** | 2173044601 (27; yes @30 min, downstream) | Khareghat Road (1; 66.2) | 3.0 / 50.0 | 29.01 / 0.19 |
| Devdhar Road  (Tackled) | no | polygon (3) | 64.7 (135) | 60/70/85 | 83 | **DETECTED** | 2173049905 (5; yes @60 min, downstream) | Deodhar Road (0; 82.1) | 3.0 / 70.0 | 29.89 / -0.31 |
| 803 Laxumi Buvan (Delete) | no | polygon (13) | 119.6 (180) | 10/25/45 | 98 | **DETECTED** | 2173046301 (19; yes @65 min, downstream) | (unnamed) (25; 115.8) | 2.0 / 70.0 | 28.64 / -0.52 |
| opp kalpataru lokmanya tilak Coloney (Delete) | no | polygon (9) | 83.5 (180) | 10/15/35 | 92 | **DETECTED** | 2173042202 (13; no) | Marg No 2 (0; 83.5) | 2.0 / 50.0 | 28.42 / -0.44 |
| 83 Hindu Coloney Dadar (Delete) | no | polygon (3) | 95.9 (180) | 15/35/55 | 95 | **DETECTED** | 2173042402 (12; no) | D.V. Pradhan Road (6; 98.2) | 3.0 / 200.0 | 28.20 / -0.41 |
| kabutar khana central matungaa near bharat bank (Delete) | no | polygon (4) | 96.2 (135) | 15/15/25 | 95 | **DETECTED** | 2173058306 (10; no) | Laxmi Narayan Lane (6; 96.2) | 2.0 / 200.0 | 28.60 / -0.57 |
| Ruiya Collage  (Tackled) | no | polygon (3) | 63.7 (180) | 15/70/100 | 83 | **DETECTED** | 2173047802 (68; no) | Lakhamasi Nappu Road (0; 106.1) | 3.0 / 100.0 | 28.80 / -0.16 |
| 89 Vaccharaj Lane | yes | polygon (2468) | 179.7 (180) | 5/10/20 | 99 | **DETECTED** | 2173059104 (8; no) | Chandavarkar Marg (2; 54.6) | 3.0 / 50.0 | 28.53 / -1.13 |
| Indian Bank Matunga Bazar Barnch (Delete) | no | polygon (4) | 51.5 (180) | 20/80/110 | 72 | **DETECTED** | 2173058004 (63; no) | Deodhar Road (5; 63.7) | 2.0 / 60.0 | 29.00 / -0.11 |
| Macherji Joshi Udyan (Delete) | no | polygon (10) | 7.6 (95) | 65/-/- | 33 | **MARGINAL** | 2174041305 (4; yes @65 min, downstream) | Lady Jehangir Road (1; 7.6) | 2.0 / 100.0 | 33.19 / -0.03 |
| Shindewadi Bus Stop (Tackled) | no | polygon (3) | 64.5 (180) | 70/75/90 | 83 | **DETECTED** | 2172039502 (16; no) | Shankar Abaji Palav Marg (4; 94.5) | 1.5 / 100.0 | 28.39 / -0.27 |
| Senapati Bapat Roda (Delete) | no | polygon (16) | 115.1 (180) | 10/15/35 | 97 | **DETECTED** | 2172048202 (29; no) | (unnamed) (0; 105.2) | 2.0 / 100.0 | 28.00 / -0.79 |
| Shivaji Park Road No 2 (Delete) | no | polygon (4) | 52.8 (120) | 30/45/75 | 73 | **DETECTED** | 2172057307 (34; no) | Lady Jamshedji Road (8; 65.1) | 2.0 / 100.0 | 29.60 / -0.16 |
| Ganesh Paethlem Shah Sadan Star Mall (Delete) | no | polygon (4) | 34.5 (120) | 25/60/105 | 58 | **DETECTED** | 2172046803! (208; yes @55 min, downstream) | Ganesh Peth Lane (3; 34.6) | 1.5 / 100.0 | 30.20 / -0.02 |
| Manish Market Beside Kohinoor Institute (Delete) | no | polygon (12) | 68.0 (180) | 20/45/85 | 85 | **DETECTED** | 2172047101 (26; no) | Senapati Bapat Marg (3; 87.9) | 2.0 / 50.0 | 28.24 / -0.48 |
| D L Vaidya Marg (Tackled) | no | polygon (5) | 31.8 (130) | 10/35/115 | 55 | **DETECTED** | 2172046806 (53; yes @50 min, downstream) | D L Vaidya Marg (3; 33.4) | None / None | 30.20 / 0.00 |
| Vijay Manjrekar Road (Tackled) | no | polygon (4) | 2.1 (120) | -/-/- | 22 | **MISSED** | 2172042403 (96; yes @70 min, downstream) | (unnamed) (6; 4.1) | None / None | 30.20 / 0.40 |
| Shakkar panchyayat R.A.Kidwai | yes | polygon (7) | 21.8 (180) | 65/135/- | 46 | **DETECTED** | 2174031409 (28; no) | R.A. Kidwai Marg (3; 47.5) | None / None | 29.96 / -0.04 |
| Dadar Railway Station | yes | polygon (19) | 23.1 (180) | 90/140/- | 47 | **DETECTED** | 2173040101 (41; no) | Dadasaheb Phalke Marg (28; 74.9) | None / None | 28.94 / -0.06 |

## Chitale Committee (2006) named locations — scenario `july2005`

Qualitative, named-location precision only: the report names roads/areas that were submerged on 26-27 July 2005; it gives no coordinates, depths or times for them. Substrings are matched against OSM segment names inside the pilot.

| Term | Segments | Example names | Max depth cm | Median cm | Share of segments >= 15 cm | Class |
|---|---|---|---|---|---|---|
| hindmata | 4 | Hindmata Flyover | 277.5 | 77.2 | 100 % | DETECTED |
| ambedkar | 105 | Dr Babasaheb Ambedkar Marg (Vincent Road); G.D. Ambedkar Marg | 143.6 | 48.2 | 64 % | DETECTED |
| lakhamsi | 6 | Lakhamsi Nappu Road | 107.1 | 47.3 | 100 % | DETECTED |
| napoo | 0 |  | - | - | 0 % | NO_ROAD_MATCH |
| king | 14 | King's Circle; King's Circle Flyover | 155.4 | 25.0 | 50 % | DETECTED |
| tilak | 41 | Lokmanya Tilak Vasahat Road No. 1; Tilak Bridge; Tilak Road; Tilak Road Extension | 106.5 | 33.5 | 63 % | DETECTED |
| senapati bapat | 59 | Senapati Bapat Marg | 127.5 | 77.1 | 97 % | DETECTED |
| parel | 5 | Shankar Rao H. Parelkar Marg | 55.7 | 44.6 | 100 % | DETECTED |
| matunga | 5 | Matunga Road; Matunga Station Road | 124.2 | 47.1 | 100 % | DETECTED |
| dadar | 6 | Dadar TT flyover; Jaganath Shankur Seth (Dadar TT) Flyover | 124.3 | 101.1 | 67 % | DETECTED |

Chitale base rate under `july2005`: 68.7 % of all 2978 named+unnamed road segments in the pilot reach >= 15 cm; a term scoring DETECTED is only meaningful relative to that share.

## Files

* `docs/validation/flooding_spots.json` — machine-readable version of everything above.
* `backend/floodnet/validation/flooding_spots.py` — the code (CLI: `python -m floodnet.validation.flooding_spots`).
