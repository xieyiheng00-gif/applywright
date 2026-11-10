# examples/

A fictional profile and a sample form, to try jobfill without entering your own data.
Back to the [main README](../README.md).

| File | What |
|---|---|
| `sample_profile.json` | A made-up candidate (Jordan Rivera), in the format `import` reads |
| `sample_form.json` | 49 fields of a typical application, in the [field JSON](../jobfill/README.md#field-json-what-a-form-filler-sends-to-resolve) format `resolve` reads |

Load the profile into a separate database, so yours is not touched:

```bash
python -m jobfill --db data/demo.db import examples/sample_profile.json --apply
```

Start an application:

```bash
python -m jobfill --db data/demo.db app start --company "Acme Robotics" --role "ML Infrastructure Engineer"
```

Answer the form:

```bash
python -m jobfill --db data/demo.db resolve examples/sample_form.json --app 1 --compact
```

Plain Python fills 40 of the 49 fields; 4 go to the agent to be written, 1 is asked, 1 is a new
question queued for `/learn`, and 3 optional ones are skipped.
