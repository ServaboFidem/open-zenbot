from importlib import resources

from src.skilltuner.licences import canonical, licence_of_text

MIT = """MIT License

Copyright (c) 2025 Someone

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

BSD2 = """BSD 2-Clause License

Copyright (c) 2025, Someone
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
OF THE POSSIBILITY OF SUCH DAMAGE.
"""

BSD3 = BSD2.replace("BSD 2-Clause", "BSD 3-Clause").replace(
    "THIS SOFTWARE IS PROVIDED",
    "3. Neither the name of the copyright holder nor the names of its\n   contributors may be used to endorse or promote products derived from\n"
    "   this software without specific prior written permission.\n\nTHIS SOFTWARE IS PROVIDED")

CCBY = resources.files("src.skilltuner").joinpath("cc-by-4.0.txt").read_text("utf-8")
# The first version of the check passed this: only the headings and Section 8 of the licence.
CCBY_SHORT = """Attribution 4.0 International

=======================================================================

Creative Commons Corporation ("Creative Commons") is not a law firm and
does not provide legal services or legal advice.

Creative Commons Attribution 4.0 International Public License

By exercising the Licensed Rights (defined below), You accept and agree
to be bound by the terms and conditions of this Creative Commons
Attribution 4.0 International Public License.

Section 1 -- Definitions.

Section 8 -- Interpretation.

  a. For the avoidance of doubt, this Public License does not, and
     shall not be interpreted to, reduce, limit, restrict, or impose
     conditions on any use of the Licensed Material that could lawfully
     be made without permission under this Public License.

  b. To the extent possible, if any provision of this Public License is
     deemed unenforceable, it shall be automatically reformed to the
     minimum extent necessary to make it enforceable. If the provision
     cannot be reformed, it shall be severed from this Public License
     without affecting the enforceability of the remaining terms and
     conditions.

  c. No term or condition of this Public License will be waived and no
     failure to comply consented to unless expressly agreed to by the
     Licensor.

  d. Nothing in this Public License constitutes or may be interpreted
     as a limitation upon, or waiver of, any privileges and immunities
     that apply to the Licensor or You, including from the legal
     processes of any jurisdiction or authority.
"""

APACHE = resources.files("src.skilltuner").joinpath("apache-2.0.txt").read_text("utf-8")
GPL = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n\nCopyright (C) 2007 Free Software Foundation"


def test_the_five_standard_texts_are_recognised():
    assert licence_of_text(MIT) == "MIT"
    assert licence_of_text(APACHE) == "Apache-2.0"
    assert licence_of_text(BSD2) == "BSD-2-Clause"
    assert licence_of_text(BSD3) == "BSD-3-Clause"
    assert licence_of_text(CCBY) == "CC-BY-4.0"


def test_a_modified_or_other_licence_is_not_recognised():
    assert licence_of_text(MIT + "\nCommons Clause License Condition v1.0\n" + "The Software is provided to you by the Licensor under the License, " * 5) == ""
    assert licence_of_text(MIT.replace("The above copyright notice and this permission notice shall be included in all\ncopies or substantial portions of the Software.",
                                       "The above copyright notice and this permission notice shall be included in all\ncopies or substantial portions of the Software.\n\nThe Software shall be used for Good, not Evil.")) == ""
    assert licence_of_text(GPL) == ""
    assert licence_of_text("") == ""
    assert licence_of_text("Do what you like with this.") == ""


def test_canonical_names():
    assert canonical("mit") == "MIT"
    assert canonical(" Apache-2.0 ") == "Apache-2.0"
    assert canonical("cc-by-4.0") == "CC-BY-4.0"
    assert canonical("GPL-3.0") == ""
    assert canonical("") == ""


def test_added_restrictions_or_mere_mentions_are_not_recognised():
    assert licence_of_text(MIT + "\nCommercial use is prohibited.\n") == ""
    assert licence_of_text("Non-commercial use only. Licensed under MIT:\n\n" + MIT) == ""
    assert licence_of_text(MIT + "\nAll rights reserved.\n") == "MIT"
    assert licence_of_text(BSD2 + "\nUse for military purposes is forbidden.\n") == ""
    assert licence_of_text("Note: free for personal use.\n" + BSD3) == ""
    assert licence_of_text(APACHE + "\n\nYou may not use this for profit.\n") == ""
    assert licence_of_text(APACHE.replace("END OF TERMS AND CONDITIONS", "Terms end here")) == ""
    assert licence_of_text(APACHE[:APACHE.index("APPENDIX")]) == "Apache-2.0"
    assert licence_of_text("This project uses the Creative Commons Attribution 4.0 International Public License.\n"
                           "Section 1 -- Definitions.\nSection 8 -- Interpretation.") == ""
    assert licence_of_text("Our README\n\n" + CCBY) == ""


def test_holder_shapes_and_trailing_cc_restrictions():
    body = MIT[MIT.index("Permission is hereby"):]
    for line in ("Copyright (c) 2015-2024 Jane Doe and contributors", "Copyright (c) 2024 Acme, Inc.",
                 "Copyright 2024 Jane Doe <jane@example.com>", "Copyright (c) 2019, 2021 J. R. Smith & Co."):
        assert licence_of_text(line + chr(10) * 2 + body) == "MIT", line
    for line in ("Copyright (c) 2024 Jane Doe. Commercial use prohibited.", "Copyright (c) 2024 Jane Doe; no use",
                 "Copyright (c) 2024 Jane Doe Commercial use prohibited", "Copyright (c) 2024 Jane: all yours!"):
        assert licence_of_text(line + chr(10) * 2 + body) == "", line
    assert licence_of_text(CCBY + chr(10) + "You may not use this commercially." + chr(10)) == ""


def test_cc_by_is_compared_whole():
    legal = CCBY[CCBY.index("Creative Commons Attribution 4.0 International Public License"):]
    notice = CCBY.rindex("=" * 71)
    assert licence_of_text(CCBY[:notice]) == "CC-BY-4.0"  # without Creative Commons' closing notice
    assert licence_of_text(legal) == "CC-BY-4.0"  # the licence alone, without the preamble
    assert licence_of_text(CCBY_SHORT) == ""
    inserted = CCBY.replace("Section 8 -- Interpretation.", "Commercial use is prohibited.\n\nSection 8 -- Interpretation.")
    assert inserted != CCBY and licence_of_text(inserted) == ""
    assert licence_of_text(CCBY.replace("Section 4 -- Sui Generis Database Rights.", "")) == ""


def test_the_apache_appendix_is_compared_whole():
    filled = APACHE.replace("Copyright [yyyy] [name of copyright owner]", "Copyright 2024 Jane Doe")
    assert filled != APACHE and licence_of_text(filled) == "Apache-2.0"
    two = APACHE.replace("Copyright [yyyy] [name of copyright owner]",
                         "Copyright (c) 2019-2024 Acme, Inc.\n   Copyright 2025 Jane Doe. All rights reserved.")
    assert licence_of_text(two) == "Apache-2.0"
    added = APACHE.replace("limitations under the License.",
                           "limitations under the License.\n\n   Commercial use is prohibited.\n\n   limitations under the License.")
    assert licence_of_text(added) == ""
    inside = APACHE.replace("identification within third-party archives.",
                            "identification within third-party archives. No use by any government is permitted.")
    assert licence_of_text(inside) == ""
    restricted = APACHE.replace("Copyright [yyyy] [name of copyright owner]", "Copyright 2024 Jane Doe no commercial use")
    assert licence_of_text(restricted) == ""
