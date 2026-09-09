"""casegen — the NL-case authoring front-end, one home for ``NL marker → common.Case``.

The pushed way to write an e2e case is NOT a hand-written ``case.yaml`` but an ``@case``/``@spec``
NL marker in the service's own source; casegen is the compiler that turns those into the
structured ``common.Case`` that ``engine`` executes as data (see ``docs/casegen.md``). The three
files here are one pipeline stage, kept together so the front-end has a structural home:

- ``contract``: ``@case`` / ``@spec`` / ``@rule`` / ``@link`` decorators + ``CaseSpec`` / ``Case`` + ``case_hash`` (the marker shape)
- ``discover``: AST scanner for ``@case`` / ``@spec`` (+ companion ``*_cases.yaml``) → ``DiscoveredCase``
- ``compiler``: ``Compiler`` seam (``StubCompiler`` / ``DraftCompiler`` / future ``LLMCompiler``) +
  ``compile``/``check``/``list`` orchestration + the ``casegen`` CLI

The judgment then runs as data via ``engine`` — no generated pytest body.
"""

from e2e_harness.casegen.compiler import CheckReport as CheckReport
from e2e_harness.casegen.compiler import Compiler as Compiler
from e2e_harness.casegen.compiler import CompileReport as CompileReport
from e2e_harness.casegen.compiler import DraftCompiler as DraftCompiler
from e2e_harness.casegen.compiler import StubCompiler as StubCompiler
from e2e_harness.casegen.compiler import check_cases as check_cases
from e2e_harness.casegen.compiler import compile_cases as compile_cases
from e2e_harness.casegen.compiler import list_cases as list_cases
from e2e_harness.casegen.compiler import main as main
from e2e_harness.casegen.contract import CASE_ID_PATTERN as CASE_ID_PATTERN
from e2e_harness.casegen.contract import Case as Case
from e2e_harness.casegen.contract import CaseSpec as CaseSpec
from e2e_harness.casegen.contract import case as case
from e2e_harness.casegen.contract import case_hash as case_hash
from e2e_harness.casegen.contract import get_cases as get_cases
from e2e_harness.casegen.contract import get_links as get_links
from e2e_harness.casegen.contract import get_rules as get_rules
from e2e_harness.casegen.contract import get_spec as get_spec
from e2e_harness.casegen.contract import get_spec_id as get_spec_id
from e2e_harness.casegen.contract import link as link
from e2e_harness.casegen.contract import load_cases_file as load_cases_file
from e2e_harness.casegen.contract import rule as rule
from e2e_harness.casegen.contract import spec as spec
from e2e_harness.casegen.discover import DiscoverConfig as DiscoverConfig
from e2e_harness.casegen.discover import DiscoveredCase as DiscoveredCase
from e2e_harness.casegen.discover import discover as discover
