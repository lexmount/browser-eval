# Web Fetch examples

The [repository quickstart](../../README.md#run-a-real-adapter-against-a-local-page) uses `dataset.json`, the owned article in `site/`, and [providers.quickstart.json](../../configs/providers.quickstart.json). It exercises the shared Web Fetch CLI with plain HTTP or installed browser adapters and generates inspectable local evidence and HTML. It requires no historical source inputs or provider account.

Serve `site/` on loopback port 8765 before running the dataset. To change the port or target, copy the dataset and update its URL. The complete article should pass; removing its final paragraph should fail. It is an integration example, not a representative browser benchmark.

Formal and historical cohorts use the [Web Fetch guide](../../harness/web_fetch/README.md), their frozen profiles and approved external reference inputs. Container resource collection assets remain under `docker/`.
