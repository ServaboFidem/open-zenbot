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

CCBY = """Attribution 4.0 International

=======================================================================

Creative Commons Corporation ("Creative Commons") is not a law firm and
does not provide legal services or legal advice.

Creative Commons Attribution 4.0 International Public License

By exercising the Licensed Rights (defined below), You accept and agree
to be bound by the terms and conditions of this Creative Commons
Attribution 4.0 International Public License.

Section 1 -- Definitions.

Section 8 -- Interpretation.
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
