# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Small business loan agent package (fsi-small-business-loan skill).

Adapted from `contrib/python/small-business-loan-agent` (AI FDE Financial
Services). Importing the package loads configuration: the workspace `.env`
written by setup.py first, then the non-placeholder defaults of the skill's
`.env.example`, so `import scripts.agent` works even before setup.
"""

from pathlib import Path

from dotenv import dotenv_values, find_dotenv, load_dotenv

# The workspace (cwd) .env written by setup.py wins over the install dir.
load_dotenv(find_dotenv(usecwd=True))

_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"
if _EXAMPLE.exists():
    import os

    for _key, _value in dotenv_values(_EXAMPLE).items():
        if _value and not _value.startswith("<") and not os.environ.get(_key):
            os.environ[_key] = _value
