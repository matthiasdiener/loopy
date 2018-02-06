__copyright__ = "Copyright (C) 2012 Andreas Kloeckner"

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""


VERSION = (2017, 2, 1)
VERSION_STATUS = ""
VERSION_TEXT = ".".join(str(x) for x in VERSION) + VERSION_STATUS

try:
    import islpy.version
except ImportError:
    _islpy_version = "_UNKNOWN_"
else:
    _islpy_version = islpy.version.VERSION_TEXT

DATA_MODEL_VERSION = "v76-islpy%s" % _islpy_version


FALLBACK_LANGUAGE_VERSION = (2017, 2, 1)
MOST_RECENT_LANGUAGE_VERSION = (2018, 1)

__doc__ = """

.. currentmodule:: loopy
.. data:: VERSION

    A tuple representing the current version number of loopy, for example
    **(2017, 2, 1)**. Direct comparison of these tuples will always yield
    valid version comparisons.

.. _language-versioning:

Loopy Language Versioning
-------------------------

At version 2018.1, :mod:`loopy` introduced a language versioning scheme to make
it easier to evolve the language while retaining backward compatibility. What
prompted this is the addition of
:attr:`loopy.Options.enforce_check_variable_access_ordered`, which (despite
its name) serves to enable a new check that helps ensure that all variable
access in a kernel is ordered as intended. Since that has the potential to
break existing programs, kernels now have to declare support for a given
language version to let them take advantage of this check.

As a result, :mod:`loopy` will now issue a warning when a call to
:func:`loopy.make_kernel` does not declare a language version. Such kernels will
(indefinitely) default to language version 2017.2.1.

Language versions will generally reflect the version number of :mod:`loopy` in
which they were introduced, though it is possible that some versions of
:mod:`loopy` do not introduce new user-visible language features. In such
situations, the previous language version number remains.


.. data:: MOST_RECENT_LANGUAGE_VERSION

    A tuple representing the most recent language version number of loopy, for
    example **(2018, 1)**. Direct comparison of these tuples will always
    yield valid version comparisons.

History of Language Versions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

* ``(2018, 1)``: :attr:`loopy.Options.enforce_check_variable_access_ordered`
    is turned on by default.

* ``(2017, 2, 1)``: Initial legacy language version.
"""
