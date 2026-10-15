import re

import pytest

from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.metrics import task_passed


def code_case():
    return {'id': 'example', 'url': 'https://example.test/', 'required_patterns': [
        {'id': 'include', 'match_scope': 'code_compact',
         'pattern': re.escape('#include<stdio.h>')},
        {'id': 'loop', 'match_scope': 'code_compact',
         'pattern': re.escape('for(i=0;i<argc;i++){')},
        {'id': 'open', 'match_scope': 'code_compact',
         'pattern': re.escape('rc=sqlite3_open(argv[1],&db);')}]}


@pytest.mark.parametrize('answer,valid', [
    ('```c\n#include <stdio.h>\nfor(i=0; i<argc; i++){\nrc = sqlite3_open(argv[1], &db);\n```', True),
    ('<html><pre>#include &lt;stdio.h&gt;\nfor(i=0; i&lt;argc; i++){\nrc = <b>sqlite3_open</b>(argv&#91;1], &amp;db);</pre></html>', True),
    ('01 #include <stdio.h>\n02 for(i=0; i<argc; i++){\n03 rc = sqlite3_open(argv[1], &db);', True),
    ('  > ```c\n  > 01 #include <stdio.h>\n  > 02 for(i=0; i<argc; i++){\n  > 03 rc = sqlite3_open(argv[1], &db);\n  > ```', True),
    ('```c\n#include\nfor(i=0; isqlite3 *db;\nrc = sqlite3_open(argv[1], &db);\n```', False),
    ('```c\n#include <stdio.h>\nfor(i=0; i<argc; i++){\nrc = **sqlite3_open**(argv[1], &db);\n```', False),
    ('sqlite3_open sqlite3_exec sqlite3_close', False),
    ('<html><a href="#include<stdio.h>for(i=0;i<argc;i++){rc=sqlite3_open(argv[1],&db);">API names</a></html>', False),
])
def test_declared_code_fragments_preserve_tokens_not_just_api_names(answer, valid):
    metrics = score_attempt(code_case(), answer.encode(), ok=True)
    assert task_passed(True, metrics) is valid
