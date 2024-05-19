# Captured output

Everything below was produced by running this code on 2026-09-24. Nothing is
illustrative and nothing has been edited except for the substitution of a long
temporary directory path with `/tmp/models`, and the removal of ANSI colour codes
from the server log.

## How it was run

There is **no AWS account involved**. The S3 API was served locally by
[`moto`](https://github.com/getmoto/moto) on `127.0.0.1:8321`, which speaks the real
S3 protocol, and the app was pointed at it with `S3_ENDPOINT_URL`. That exercises
the genuine boto3 download path — but it means **every "download" timing below is a
loopback transfer, not a real S3-to-Lambda transfer**. Treat the fetch numbers as a
lower bound of zero network cost, and measure your own with `/warmup` after deploying.

| | |
|---|---|
| Machine | Apple Silicon laptop, macOS 15 (Darwin 25.4.0) |
| Python | 3.12 |
| spaCy | 3.8.16 |
| Model | `en_core_web_sm-3.8.0` (and `en_core_web_md-3.8.0` where noted) |
| Object store | `moto` server on `127.0.0.1:8321`, standing in for S3 |

## 1. Seeding the bucket

The model is uploaded to object storage once, by an operator. It is never part of
the deployment package.

```console
$ python -m nlp_lambda.cli download --dest /tmp/models
/tmp/models/en_core_web_sm-3.8.0.tar.gz (12.8 MB)

$ python -m nlp_lambda.cli upload --dest /tmp/models
INFO botocore.credentials Found credentials in environment variables.
uploaded s3://nlp-lambda-models/models/en_core_web_sm-3.8.0.tar.gz (12.8 MB)
```

## 2. Cold start, seen from the server log

A request arrives at a process that has never loaded a model. The three log lines
in the middle are the whole architecture: fetch from S3, unpack into `/tmp`, load
into memory.

```console
2026-09-24 06:12:31,539 INFO nlp_lambda.api sentry_disabled reason=no_dsn
 * Serving Flask app 'nlp_lambda.api'
 * Debug mode: off
2026-09-24 06:12:31,546 INFO werkzeug WARNING: This is a development server. Do not use it in a production deployment. Use a production WSGI server instead.
 * Running on http://127.0.0.1:8320
2026-09-24 06:12:31,546 INFO werkzeug Press CTRL+C to quit
2026-09-24 06:12:32,001 INFO werkzeug 127.0.0.1 - - [24/Sep/2026 06:12:32] "GET /health HTTP/1.1" 200 -
2026-09-24 06:12:32,058 INFO werkzeug 127.0.0.1 - - [24/Sep/2026 06:12:32] "GET / HTTP/1.1" 200 -
2026-09-24 06:12:32,108 INFO werkzeug 127.0.0.1 - - [24/Sep/2026 06:12:32] "GET /health HTTP/1.1" 200 -
2026-09-24 06:12:32,706 INFO botocore.credentials Found credentials in environment variables.
2026-09-24 06:12:32,909 INFO nlp_lambda.model_cache model_cache miss model=en_core_web_sm-3.8.0 source=s3 seconds=0.280 bytes=15267634
2026-09-24 06:12:32,910 INFO nlp_lambda.bootstrap model_ready model=en_core_web_sm-3.8.0 cache_hit=False seconds=0.280 path=/tmp/nlp_lambda_live/models/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0/en_core_web_sm/en_core_web_sm-3.8.0
2026-09-24 06:12:33,150 INFO nlp_lambda.service pipeline_loaded name=core_web_sm seconds=1.002 components=tok2vec,tagger,parser,attribute_ruler,lemmatizer,ner
2026-09-24 06:12:33,150 INFO werkzeug 127.0.0.1 - - [24/Sep/2026 06:12:33] "POST /warmup HTTP/1.1" 200 -
2026-09-24 06:12:33,167 INFO werkzeug 127.0.0.1 - - [24/Sep/2026 06:12:33] "POST /warmup HTTP/1.1" 200 -
2026-09-24 06:12:33,210 INFO werkzeug 127.0.0.1 - - [24/Sep/2026 06:12:33] "GET /health HTTP/1.1" 200 -
2026-09-24 06:12:33,253 INFO nlp_lambda.service analyze analyzer=entities documents=1 chars=97 seconds=0.0031 disabled=tok2vec,tagger,parser,attribute_ruler,lemmatizer
2026-09-24 06:12:33,253 INFO werkzeug 127.0.0.1 - - [24/Sep/2026 06:12:33] "POST /analyze HTTP/1.1" 200 -
2026-09-24 06:12:33,292 INFO nlp_lambda.service analyze analyzer=locations documents=2 chars=107 seconds=0.0030 disabled=tok2vec,tagger,parser,attribute_ruler,lemmatizer
2026-09-24 06:12:33,292 INFO werkzeug 127.0.0.1 - - [24/Sep/2026 06:12:33] "POST /analyze HTTP/1.1" 200 -
2026-09-24 06:12:33,333 INFO nlp_lambda.service analyze analyzer=noun_chunks documents=1 chars=45 seconds=0.0029 disabled=lemmatizer,ner
2026-09-24 06:12:33,333 INFO werkzeug 127.0.0.1 - - [24/Sep/2026 06:12:33] "POST /analyze HTTP/1.1" 200 -
2026-09-24 06:12:33,371 INFO nlp_lambda.service analyze analyzer=locations documents=1 chars=38 seconds=0.0021 disabled=tok2vec,tagger,parser,attribute_ruler,lemmatizer
2026-09-24 06:12:33,371 INFO werkzeug 127.0.0.1 - - [24/Sep/2026 06:12:33] "POST /find_locations HTTP/1.1" 200 -
2026-09-24 06:12:33,408 INFO werkzeug 127.0.0.1 - - [24/Sep/2026 06:12:33] "POST /find_locations HTTP/1.1" 400 -
2026-09-24 06:12:33,447 INFO werkzeug 127.0.0.1 - - [24/Sep/2026 06:12:33] "POST /analyze HTTP/1.1" 400 -
2026-09-24 06:12:33,486 INFO werkzeug 127.0.0.1 - - [24/Sep/2026 06:12:33] "GET /nope HTTP/1.1" 404 -
```

Read the three interesting lines:

* `model_cache miss ... seconds=0.280 bytes=15267634` — 15.3 MB fetched from the
  bucket and unpacked into `/tmp`, on a loopback connection.
* `model_ready ... cache_hit=False` — the resolved pipeline directory inside the
  unpacked sdist.
* `pipeline_loaded ... seconds=1.002` — total time for the loader, i.e. the fetch
  *plus* `spacy.load`.

Every subsequent `analyze` line takes single-digit milliseconds, and the `disabled=`
field shows which components were skipped for that analyzer.

## 3. HTTP session

```console
$ curl -s http://127.0.0.1:8320/ | python3 -m json.tool
{
    "analyzers": {
        "entities": "Every named entity with its offsets",
        "locations": "GPE/LOC mentions, most frequent first",
        "noun_chunks": "Base noun phrases"
    },
    "routes": {
        "GET /health": "liveness; does not touch S3 or the model",
        "POST /analyze": "{text|texts, analyzer} -> one result per text",
        "POST /find_locations": "{text} -> place names, most frequent first",
        "POST /warmup": "force the model load and report what it cost"
    },
    "service": "nlp-aws-lambda-hosting",
    "version": "1.0.0"
}

$ curl -s http://127.0.0.1:8320/health | python3 -m json.tool
{
    "analyzers": [
        "entities",
        "locations",
        "noun_chunks"
    ],
    "model": "en_core_web_sm-3.8.0",
    "model_loaded": false,
    "model_source": "s3",
    "status": "ok",
    "version": "1.0.0"
}

$ curl -s -X POST http://127.0.0.1:8320/warmup | python3 -m json.tool
{
    "already_warm": false,
    "components": [
        "tok2vec",
        "tagger",
        "parser",
        "attribute_ruler",
        "lemmatizer",
        "ner"
    ],
    "elapsed_seconds": 1.002,
    "load_seconds": 1.0017304169014096,
    "pipeline": "core_web_sm"
}

$ curl -s -X POST http://127.0.0.1:8320/warmup | python3 -m json.tool
{
    "already_warm": true,
    "components": [
        "tok2vec",
        "tagger",
        "parser",
        "attribute_ruler",
        "lemmatizer",
        "ner"
    ],
    "elapsed_seconds": 0.0,
    "load_seconds": 1.0017304169014096,
    "pipeline": "core_web_sm"
}

$ curl -s http://127.0.0.1:8320/health | python3 -m json.tool
{
    "analyzers": [
        "entities",
        "locations",
        "noun_chunks"
    ],
    "model": "en_core_web_sm-3.8.0",
    "model_loaded": true,
    "model_source": "s3",
    "status": "ok",
    "version": "1.0.0"
}

$ curl -s -X POST http://127.0.0.1:8320/analyze -H 'Content-Type: application/json' -d '{"text": "Ada Lovelace met Charles Babbage in London in 1843, and the Analytical Engine was never finished."}' | python3 -m json.tool
{
    "analyzer": "entities",
    "count": 1,
    "results": [
        [
            {
                "end_char": 12,
                "label": "PERSON",
                "start_char": 0,
                "text": "Ada Lovelace"
            },
            {
                "end_char": 32,
                "label": "PERSON",
                "start_char": 17,
                "text": "Charles Babbage"
            },
            {
                "end_char": 42,
                "label": "GPE",
                "start_char": 36,
                "text": "London"
            },
            {
                "end_char": 50,
                "label": "DATE",
                "start_char": 46,
                "text": "1843"
            },
            {
                "end_char": 77,
                "label": "ORG",
                "start_char": 56,
                "text": "the Analytical Engine"
            }
        ]
    ]
}

$ curl -s -X POST http://127.0.0.1:8320/analyze -H 'Content-Type: application/json' -d '{"texts": ["Flights from Madrid to Lisbon were cancelled; Madrid reopened first.", "No place names in this sentence at all."], "analyzer": "locations"}' | python3 -m json.tool
{
    "analyzer": "locations",
    "count": 2,
    "results": [
        [
            "Madrid",
            "Lisbon"
        ],
        []
    ]
}

$ curl -s -X POST http://127.0.0.1:8320/analyze -H 'Content-Type: application/json' -d '{"text": "The quick brown fox jumped over the lazy dog.", "analyzer": "noun_chunks"}' | python3 -m json.tool
{
    "analyzer": "noun_chunks",
    "count": 1,
    "results": [
        [
            "The quick brown fox",
            "the lazy dog"
        ]
    ]
}

$ curl -s -X POST http://127.0.0.1:8320/find_locations -H 'Content-Type: application/json' -d '{"text": "Spain is a sunnier country than the UK"}' | python3 -m json.tool
[
    "Spain",
    "UK"
]

$ curl -s -X POST http://127.0.0.1:8320/find_locations -H 'Content-Type: application/json' -d '{"txt": "typo in the key"}' | python3 -m json.tool
{
    "detail": "request body must contain 'text' or 'texts'",
    "error": "bad_request"
}

$ curl -s -X POST http://127.0.0.1:8320/analyze -H 'Content-Type: application/json' -d '{"text": "hi", "analyzer": "astrology"}' | python3 -m json.tool
{
    "detail": "unknown analyzer 'astrology'; available: entities, locations, noun_chunks",
    "error": "bad_request"
}

$ curl -s http://127.0.0.1:8320/nope | python3 -m json.tool
{
    "error": "not_found"
}

```

## 4. Cold vs warm, isolated

`cli warm` does exactly what a cold invocation does and prints the breakdown.
Running it twice shows the cost that the `/tmp` cache removes.

```console
$ python -m nlp_lambda.cli warm        # cold: /tmp is empty
INFO botocore.credentials Found credentials in environment variables.
INFO nlp_lambda.model_cache model_cache miss model=en_core_web_sm-3.8.0 source=s3 seconds=0.289 bytes=15267634
{
  "model": "en_core_web_sm-3.8.0",
  "source": "s3",
  "cache_hit": false,
  "fetch_seconds": 0.291,
  "spacy_load_seconds": 0.742,
  "first_doc_seconds": 0.004,
  "bytes_on_disk": 15267634,
  "components": [
    "tok2vec",
    "tagger",
    "parser",
    "attribute_ruler",
    "lemmatizer",
    "ner"
  ],
  "entities": [
    [
      "Madrid",
      "GPE"
    ]
  ]
}

$ python -m nlp_lambda.cli warm        # the model is already unpacked in /tmp
INFO nlp_lambda.model_cache model_cache disk_hit model=en_core_web_sm-3.8.0 seconds=0.001
{
  "model": "en_core_web_sm-3.8.0",
  "source": "s3",
  "cache_hit": true,
  "fetch_seconds": 0.002,
  "spacy_load_seconds": 0.629,
  "first_doc_seconds": 0.003,
  "bytes_on_disk": 15267634,
  "components": [
    "tok2vec",
    "tagger",
    "parser",
    "attribute_ruler",
    "lemmatizer",
    "ner"
  ],
  "entities": [
    [
      "Madrid",
      "GPE"
    ]
  ]
}
```

The same with the medium model, which is 3.7x the bytes:

```console
$ SPACY_MODEL=en_core_web_md-3.8.0 python -m nlp_lambda.cli warm     # cold
  "model": "en_core_web_md-3.8.0",
  "cache_hit": false,
  "fetch_seconds": 0.466,
  "spacy_load_seconds": 1.035,
  "first_doc_seconds": 0.004,
  "bytes_on_disk": 56577245,
```

## 5. What disabling unused components buys

```console
$ MODEL_SOURCE=local LOCAL_MODEL_ROOT=/tmp/models python -m scripts.bench_pipeline
batch of 32 documents, 20448 characters total, 5 rounds
  all 6 components enabled          : median   414.9 ms   best   412.8 ms
  entities analyzer (ner only)      : median   167.7 ms   best   165.6 ms
  speedup (median)                  : 2.47x
  per document, entities analyzer   : 5.24 ms
```

## 6. Test suite and linter

```console
$ pytest
...................................................................      [100%]
67 passed in 2.51s

$ ruff check .
All checks passed!

$ ruff format --check .
22 files already formatted
```

The same suite through pipenv, against the regenerated `Pipfile.lock`:

```console
$ pipenv install --dev
Installing dependencies from Pipfile.lock (274320)...
$ pipenv run pytest
...................................................................      [100%]
67 passed in 27.18s
```

For contrast, the `Pipfile` as it was before this pass cannot be used at all on a
current machine, because it pinned a Python that is no longer installed:

```console
$ pipenv lock          # with the original Pipfile (python_version = "3.8")
Warning: Python 3.8 was not found on your system...

You can specify specific versions of Python with:
$ pipenv --python path/to/python
```

## 7. Sizes (why the model cannot be in the package)

Measured, not estimated:

* `requirements.txt` installed into a clean virtualenv: **169 MB** on disk, of which
  **20 MB** is `pip`/`setuptools`/`pkg_resources` that a deployment package would not
  carry — call it **~149 MB of runtime dependencies before any model**. (`du -sm` on
  the venv's `site-packages`.)
* Model archive sizes, from an HTTP HEAD against the spaCy releases (no download):

  | model | archive | unpacked in `/tmp` |
  |---|---|---|
  | `en_core_web_sm-3.8.0` | 12.8 MB | 15.3 MB (measured) |
  | `en_core_web_md-3.8.0` | 33.5 MB | 56.6 MB (measured) |
  | `en_core_web_lg-3.8.0` | 400.6 MB | not measured |
  | `en_core_web_trf-3.8.0` | 457.4 MB | not measured |

The arithmetic that motivates the whole project: 149 MB of dependencies + 56.6 MB of
unpacked `en_core_web_md` is ~206 MB against Lambda's 250 MB unzipped package limit —
it fits, barely, with no room for a second model or a bigger one. `en_core_web_lg`
does not fit at all: its *compressed* archive is already 400.6 MB, well past the
250 MB limit, and larger than the 512 MB `/tmp` allowance once you account for
holding the archive and the unpacked copy at the same time.
