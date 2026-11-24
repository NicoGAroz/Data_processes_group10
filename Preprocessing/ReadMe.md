## Setup (Windows)

1. Install PostgreSQL 16 create a database and a user. In etl.py change username, database name and password from current placeholders. 
2. Pip install all the requirements in reqirements.txt
3. Run according to run order. 


## Run order

```
python etl.py init
python etl.py load-csv --file path\to\patient_information.csv
python etl.py load-csv --file path\to\patient_history.csv
python etl.py transform        # raw -> clean -> mart, prints the rule log
python etl.py checks           # 12 quality tests, exit code 1 on FAIL
```

Dry run without real data: `python make_synthetic_data.py --n 6000 --out synthetic` and load the files from `synthetic/`.

## Layers and rules

| Layer | Content | File |
|---|---|---|
| raw | typed copy of the delivery, lineage columns | `sql/01_raw_tables.sql` |
| clean | S-rules (surgery) and H-rules (history), every rule logged in `etl.rule_log` | `sql/02_clean_surgery.sql`, `sql/03_clean_history.sql` |
| mart | `patient_history_features` (one row per patient), `model_dataset` (one row per surgery) | `sql/04_mart.sql` |

Post-surgery columns (discharge, length of stay, OR-out, anaesthesia-stop) stay in `clean` for analysis and are
**absent from `mart.model_dataset`** (quality test Q07 enforces this).


## ToDo: 

Open questions are marked with TODO. Just search for it in the code. The goal is not for all of us to run the code on the real data individually, but for one person to do it and then share the data with the others using the following approach: 

# One Person executes: 
$tag = "mover_clean_" + (Get-Date -Format "yyyy-MM-dd")
pg_dump -h localhost -U mover -d mover -n mart -n clean -n etl -Fc --no-owner --no-privileges -f "$tag.dump"
Get-FileHash "$tag.dump" -Algorithm SHA256                                                                      

send the checksum to the team separatel


# Everyone else executes: 
# teammate, on their own machine
createdb -h localhost -U postgres mover_shared
pg_restore -h localhost -U postgres -d mover_shared --no-owner -v mover_clean_2026-11-24.dump
