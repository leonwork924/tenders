Drop licensed CSV or JSON exports from paid aggregators here
(GlobalTenders, TendersInfo, TenderHive, TRINTA, AfricaTender, ...).

Name the file so it starts with the provider, e.g.
    globaltenders_2026-08-27.csv
    tendersinfo_export.json

The next `fetch` run reads it, maps the columns using
sources.csv_inbox.column_map in config.yaml, scores the rows like any other
source, and moves the file into inbox/processed/.
