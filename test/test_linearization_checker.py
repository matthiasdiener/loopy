from __future__ import division, print_function

__copyright__ = "Copyright (C) 2019 James Stevens"

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

import six  # noqa: F401
import sys
import numpy as np
import loopy as lp
import islpy as isl
from pyopencl.tools import (  # noqa
    pytest_generate_tests_for_pyopencl
    as pytest_generate_tests)
from loopy.version import LOOPY_USE_LANGUAGE_VERSION_2018_2  # noqa
import logging
from loopy import (
    preprocess_kernel,
    get_one_linearized_kernel,
)
from loopy.schedule.checker.schedule import (
    LEX_VAR_PREFIX,
    BLEX_VAR_PREFIX,
    STATEMENT_VAR_NAME,
    LTAG_VAR_NAMES,
    GTAG_VAR_NAMES,
    BEFORE_MARK,
)
from loopy.schedule.checker.utils import (
    ensure_dim_names_match_and_align,
)

logger = logging.getLogger(__name__)


# {{{ helper functions for map creation/handling

def _align_and_compare_maps(maps1, maps2):
    from loopy.schedule.checker.utils import prettier_map_string

    for map1, map2 in zip(maps1, maps2):
        # Align maps and compare
        map1_aligned = ensure_dim_names_match_and_align(map1, map2)
        if map1_aligned != map2:
            print("Maps not equal:")
            print(prettier_map_string(map1_aligned))
            print(prettier_map_string(map2))
        assert map1_aligned == map2


def _lex_point_string(dim_vals, lid_inames=[], gid_inames=[], prefix=LEX_VAR_PREFIX):
    # Return a string describing a point in a lex space
    # by assigning values to lex dimension variables
    # (used to create maps below)

    return ", ".join(
        ["%s%d=%s" % (prefix, idx, str(val))
        for idx, val in enumerate(dim_vals)] +
        ["%s=%s" % (LTAG_VAR_NAMES[idx], iname)
        for idx, iname in enumerate(lid_inames)] +
        ["%s=%s" % (GTAG_VAR_NAMES[idx], iname)
        for idx, iname in enumerate(gid_inames)]
        )


def _isl_map_with_marked_dims(s):
    from loopy.schedule.checker.utils import (
        append_marker_to_isl_map_var_names,
    )
    dt = isl.dim_type
    # Isl ignores the apostrophes in map strings, until they are explicitly added
    return append_marker_to_isl_map_var_names(isl.Map(s), dt.in_, BEFORE_MARK)

# }}}


# {{{ test pairwise schedule creation

def test_pairwise_schedule_creation():
    from loopy.schedule.checker import (
        get_schedules_for_statement_pairs,
    )

    # Example kernel
    # insn_c depends on insn_b only to create deterministic order
    # insn_d depends on insn_c only to create deterministic order
    knl = lp.make_kernel(
        [
            "{[i]: 0<=i<pi}",
            "{[k]: 0<=k<pk}",
            "{[j]: 0<=j<pj}",
            "{[t]: 0<=t<pt}",
        ],
        """
        for i
            for k
                <>temp = b[i,k]  {id=insn_a}
            end
            for j
                a[i,j] = temp + 1  {id=insn_b,dep=insn_a}
                c[i,j] = d[i,j]  {id=insn_c,dep=insn_b}
            end
        end
        for t
            e[t] = f[t]  {id=insn_d, dep=insn_c}
        end
        """,
        name="example",
        assumptions="pi,pj,pk,pt >= 1",
        )
    knl = lp.add_and_infer_dtypes(
            knl,
            {"b": np.float32, "d": np.float32, "f": np.float32})
    knl = lp.prioritize_loops(knl, "i,k")
    knl = lp.prioritize_loops(knl, "i,j")

    # Get a linearization
    proc_knl = preprocess_kernel(knl)
    lin_knl = get_one_linearized_kernel(proc_knl)
    linearization_items = lin_knl.linearization

    insn_id_pairs = [
        ("insn_a", "insn_b"),
        ("insn_a", "insn_c"),
        ("insn_a", "insn_d"),
        ("insn_b", "insn_c"),
        ("insn_b", "insn_d"),
        ("insn_c", "insn_d"),
        ]
    scheds = get_schedules_for_statement_pairs(
        lin_knl,
        linearization_items,
        insn_id_pairs,
        return_schedules=True,
        )

    # Relationship between insn_a and insn_b ---------------------------------------

    # Get maps (include schedules, just for test purposes)
    (
        sio_seq, (sched_before, sched_after)
    ), (
        sio_lconc, (lconc_sched_before, lconc_sched_after)
    ), (
        sio_gconc, (gconc_sched_before, gconc_sched_after)
    ) = scheds[
        ("insn_a", "insn_b")]

    # Create expected maps and compare

    sched_before_exp = isl.Map(
        "[pi, pk] -> { [%s=0, i, k] -> [%s] : 0 <= i < pi and 0 <= k < pk }"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string(["i", "0"]),
            )
        )

    sched_after_exp = isl.Map(
        "[pi, pj] -> { [%s=1, i, j] -> [%s] : 0 <= i < pi and 0 <= j < pj }"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string(["i", "1"]),
            )
        )

    _align_and_compare_maps(
        [sched_before_exp, sched_after_exp],
        [sched_before, sched_after],
        )

    # ------------------------------------------------------------------------------
    # Relationship between insn_a and insn_c ---------------------------------------

    # Get maps (include schedules, just for test purposes)
    (
        sio_seq, (sched_before, sched_after)
    ), (
        sio_lconc, (lconc_sched_before, lconc_sched_after)
    ), (
        sio_gconc, (gconc_sched_before, gconc_sched_after)
    ) = scheds[
        ("insn_a", "insn_c")]

    # Create expected maps and compare

    sched_before_exp = isl.Map(
        "[pi, pk] -> { [%s=0, i, k] -> [%s] : 0 <= i < pi and 0 <= k < pk }"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string(["i", "0"]),
            )
        )

    sched_after_exp = isl.Map(
        "[pi, pj] -> { [%s=1, i, j] -> [%s] : 0 <= i < pi and 0 <= j < pj }"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string(["i", "1"]),
            )
        )

    _align_and_compare_maps(
        [sched_before_exp, sched_after_exp],
        [sched_before, sched_after],
        )

    # ------------------------------------------------------------------------------
    # Relationship between insn_a and insn_d ---------------------------------------

    # Get maps (include schedules, just for test purposes)
    (
        sio_seq, (sched_before, sched_after)
    ), (
        sio_lconc, (lconc_sched_before, lconc_sched_after)
    ), (
        sio_gconc, (gconc_sched_before, gconc_sched_after)
    ) = scheds[
        ("insn_a", "insn_d")]

    # Create expected maps and compare

    sched_before_exp = isl.Map(
        "[pi, pk] -> { [%s=0, i, k] -> [%s] : 0 <= i < pi and 0 <= k < pk }"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string([0, ]),
            )
        )

    sched_after_exp = isl.Map(
        "[pt] -> { [%s=1, t] -> [%s] : 0 <= t < pt }"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string([1, ]),
            )
        )

    _align_and_compare_maps(
        [sched_before_exp, sched_after_exp],
        [sched_before, sched_after],
        )

    # ------------------------------------------------------------------------------
    # Relationship between insn_b and insn_c ---------------------------------------

    # Get maps (include schedules, just for test purposes)
    (
        sio_seq, (sched_before, sched_after)
    ), (
        sio_lconc, (lconc_sched_before, lconc_sched_after)
    ), (
        sio_gconc, (gconc_sched_before, gconc_sched_after)
    ) = scheds[
        ("insn_b", "insn_c")]

    # Create expected maps and compare

    sched_before_exp = isl.Map(
        "[pi, pj] -> { [%s=0, i, j] -> [%s] : 0 <= i < pi and 0 <= j < pj }"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string(["i", "j", 0]),
            )
        )

    sched_after_exp = isl.Map(
        "[pi, pj] -> { [%s=1, i, j] -> [%s] : 0 <= i < pi and 0 <= j < pj }"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string(["i", "j", 1]),
            )
        )

    _align_and_compare_maps(
        [sched_before_exp, sched_after_exp],
        [sched_before, sched_after],
        )

    # ------------------------------------------------------------------------------
    # Relationship between insn_b and insn_d ---------------------------------------

    # Get maps (include schedules, just for test purposes)
    (
        sio_seq, (sched_before, sched_after)
    ), (
        sio_lconc, (lconc_sched_before, lconc_sched_after)
    ), (
        sio_gconc, (gconc_sched_before, gconc_sched_after)
    ) = scheds[
        ("insn_b", "insn_d")]

    # Create expected maps and compare

    sched_before_exp = isl.Map(
        "[pi, pj] -> { [%s=0, i, j] -> [%s] : 0 <= i < pi and 0 <= j < pj }"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string([0, ]),
            )
        )

    sched_after_exp = isl.Map(
        "[pt] -> { [%s=1, t] -> [%s] : 0 <= t < pt }"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string([1, ]),
            )
        )

    _align_and_compare_maps(
        [sched_before_exp, sched_after_exp],
        [sched_before, sched_after],
        )

    # ------------------------------------------------------------------------------
    # Relationship between insn_c and insn_d ---------------------------------------

    # Get maps (include schedules, just for test purposes)
    (
        sio_seq, (sched_before, sched_after)
    ), (
        sio_lconc, (lconc_sched_before, lconc_sched_after)
    ), (
        sio_gconc, (gconc_sched_before, gconc_sched_after)
    ) = scheds[
        ("insn_c", "insn_d")]

    # Create expected maps and compare

    sched_before_exp = isl.Map(
        "[pi, pj] -> { [%s=0, i, j] -> [%s] : 0 <= i < pi and 0 <= j < pj }"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string([0, ]),
            )
        )

    sched_after_exp = isl.Map(
        "[pt] -> { [%s=1, t] -> [%s] : 0 <= t < pt }"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string([1, ]),
            )
        )

    _align_and_compare_maps(
        [sched_before_exp, sched_after_exp],
        [sched_before, sched_after],
        )


def test_pairwise_schedule_creation_with_hw_par_tags():
    from loopy.schedule.checker import (
        get_schedules_for_statement_pairs,
    )

    # Example kernel
    knl = lp.make_kernel(
        [
            "{[i,ii]: 0<=i,ii<pi}",
            "{[j,jj]: 0<=j,jj<pj}",
        ],
        """
        for i
            for ii
                for j
                    for jj
                        <>temp = b[i,ii,j,jj]  {id=stmt_a}
                        a[i,ii,j,jj] = temp + 1  {id=stmt_b,dep=stmt_a}
                    end
                end
            end
        end
        """,
        name="example",
        assumptions="pi,pj >= 1",
        lang_version=(2018, 2)
        )
    knl = lp.add_and_infer_dtypes(knl, {"a": np.float32, "b": np.float32})
    knl = lp.tag_inames(knl, {"j": "l.1", "jj": "l.0", "i": "g.0"})

    # Get a linearization
    proc_knl = preprocess_kernel(knl)
    lin_knl = get_one_linearized_kernel(proc_knl)
    linearization_items = lin_knl.linearization

    stmt_id_pairs = [
        ("stmt_a", "stmt_b"),
        ]
    scheds = get_schedules_for_statement_pairs(
        lin_knl,
        linearization_items,
        stmt_id_pairs,
        return_schedules=True,
        )

    # Relationship between stmt_a and stmt_b ---------------------------------------

    # Get maps (include schedules, just for test purposes)
    (
        sio_seq, (sched_before, sched_after)
    ), (
        sio_lconc, (lconc_sched_before, lconc_sched_after)
    ), (
        sio_gconc, (gconc_sched_before, gconc_sched_after)
    ) = scheds[
        ("stmt_a", "stmt_b")]

    # Create expected maps and compare

    sched_before_exp = isl.Map(
        "[pi,pj] -> {[%s=0,i,ii,j,jj] -> [%s] : 0 <= i,ii < pi and 0 <= j,jj < pj}"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string(
                ["ii", "0"],
                lid_inames=["jj", "j"], gid_inames=["i"],
                ),
            )
        )

    sched_after_exp = isl.Map(
        "[pi,pj] -> {[%s=1,i,ii,j,jj] -> [%s] : 0 <= i,ii < pi and 0 <= j,jj < pj}"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string(
                ["ii", "1"],
                lid_inames=["jj", "j"], gid_inames=["i"],
                ),
            )
        )

    _align_and_compare_maps(
        [sched_before_exp, sched_after_exp],
        [sched_before, sched_after],
        )

    # ------------------------------------------------------------------------------

# }}}


# {{{ test lex order map creation

def test_lex_order_map_creation():
    from loopy.schedule.checker.lexicographic_order_map import (
        create_lex_order_map,
    )

    def _check_lex_map(exp_lex_order_map, n_dims):

        lex_order_map = create_lex_order_map(
            n_dims=n_dims,
            dim_names=["%s%d" % (LEX_VAR_PREFIX, i) for i in range(n_dims)],
            )

        assert lex_order_map == exp_lex_order_map
        assert lex_order_map.get_var_dict() == exp_lex_order_map.get_var_dict()

    exp_lex_order_map = _isl_map_with_marked_dims(
        "{{ "
        "[{0}0', {0}1', {0}2', {0}3', {0}4'] -> [{0}0, {0}1, {0}2, {0}3, {0}4] :"
        "("
        "{0}0' < {0}0 "
        ") or ("
        "{0}0'={0}0 and {0}1' < {0}1 "
        ") or ("
        "{0}0'={0}0 and {0}1'={0}1 and {0}2' < {0}2 "
        ") or ("
        "{0}0'={0}0 and {0}1'={0}1 and {0}2'={0}2 and {0}3' < {0}3 "
        ") or ("
        "{0}0'={0}0 and {0}1'={0}1 and {0}2'={0}2 and {0}3'={0}3 and {0}4' < {0}4"
        ")"
        "}}".format(LEX_VAR_PREFIX))

    _check_lex_map(exp_lex_order_map, 5)

    exp_lex_order_map = _isl_map_with_marked_dims(
        "{{ "
        "[{0}0'] -> [{0}0] :"
        "("
        "{0}0' < {0}0 "
        ")"
        "}}".format(LEX_VAR_PREFIX))

    _check_lex_map(exp_lex_order_map, 1)

# }}}


# {{{ test statement instance ordering creation

def _check_sio_for_stmt_pair(
        stmt_id_before,
        stmt_id_after,
        sio_dict,
        exp_sio_seq=None,
        exp_sched_before_seq=None,
        exp_sched_after_seq=None,
        exp_sio_lconc=None,
        exp_sched_before_lconc=None,
        exp_sched_after_lconc=None,
        exp_sio_gconc=None,
        exp_sched_before_gconc=None,
        exp_sched_after_gconc=None,
        ):
    from loopy.schedule.checker.utils import (
        ensure_dim_names_match_and_align,
    )

    # Check whether scheds were included
    #try:
    (
        sio_seq, (sched_before, sched_after)
    ), (
        sio_lconc, (sched_before_lconc, sched_after_lconc)
    ), (
        sio_gconc, (sched_before_gconc, sched_after_gconc)
    ) = sio_dict[
        (stmt_id_before, stmt_id_after)]
    #except :
    #    sio_seq, sio_lconc, sio_gconc = sio_dict[
    #        (stmt_id_before, stmt_id_after)]

    # TODO left off here, check all passed maps,
    # en eliminate _align_and_comp...

    sio_seq_aligned = ensure_dim_names_match_and_align(exp_sio_seq, sio_seq)

    assert sio_seq_aligned == exp_sio_seq


def test_statement_instance_ordering():
    from loopy.schedule.checker import (
        get_schedules_for_statement_pairs,
    )

    # Example kernel (add deps to fix loop order)
    knl = lp.make_kernel(
        [
            "{[i]: 0<=i<pi}",
            "{[k]: 0<=k<pk}",
            "{[j]: 0<=j<pj}",
            "{[t]: 0<=t<pt}",
        ],
        """
        for i
            for k
                <>temp = b[i,k]  {id=stmt_a}
            end
            for j
                a[i,j] = temp + 1  {id=stmt_b,dep=stmt_a}
                c[i,j] = d[i,j]  {id=stmt_c,dep=stmt_b}
            end
        end
        for t
            e[t] = f[t]  {id=stmt_d, dep=stmt_c}
        end
        """,
        name="example",
        assumptions="pi,pj,pk,pt >= 1",
        lang_version=(2018, 2)
        )
    knl = lp.add_and_infer_dtypes(
            knl,
            {"b": np.float32, "d": np.float32, "f": np.float32})
    knl = lp.prioritize_loops(knl, "i,k")
    knl = lp.prioritize_loops(knl, "i,j")

    # Get a linearization
    knl = preprocess_kernel(knl)
    knl = get_one_linearized_kernel(knl)
    linearization_items = knl.linearization

    # Get pairwise schedules
    stmt_id_pairs = [
        ("stmt_a", "stmt_b"),
        ("stmt_a", "stmt_c"),
        ("stmt_a", "stmt_d"),
        ("stmt_b", "stmt_c"),
        ("stmt_b", "stmt_d"),
        ("stmt_c", "stmt_d"),
        ]
    scheds = get_schedules_for_statement_pairs(
        knl,
        linearization_items,
        stmt_id_pairs,
        return_schedules=True,
        )

    # Relationship between stmt_a and stmt_b ---------------------------------------

    exp_sio_seq = _isl_map_with_marked_dims(
        "[pi, pj, pk] -> {{ "
        "[{0}'=0, i', k'] -> [{0}=1, i, j] : "
        "0 <= i,i' < pi and 0 <= k' < pk and 0 <= j < pj and i >= i' "
        "}}".format(STATEMENT_VAR_NAME)
        )

    _check_sio_for_stmt_pair("stmt_a", "stmt_b", scheds, exp_sio_seq=exp_sio_seq)

    # Relationship between stmt_a and stmt_c ---------------------------------------

    exp_sio_seq = _isl_map_with_marked_dims(
        "[pi, pj, pk] -> {{ "
        "[{0}'=0, i', k'] -> [{0}=1, i, j] : "
        "0 <= i,i' < pi and 0 <= k' < pk and 0 <= j < pj and i >= i' "
        "}}".format(STATEMENT_VAR_NAME)
        )

    _check_sio_for_stmt_pair("stmt_a", "stmt_c", scheds, exp_sio_seq=exp_sio_seq)

    # Relationship between stmt_a and stmt_d ---------------------------------------

    exp_sio_seq = _isl_map_with_marked_dims(
        "[pt, pi, pk] -> {{ "
        "[{0}'=0, i', k'] -> [{0}=1, t] : "
        "0 <= i' < pi and 0 <= k' < pk and 0 <= t < pt "
        "}}".format(STATEMENT_VAR_NAME)
        )

    _check_sio_for_stmt_pair("stmt_a", "stmt_d", scheds, exp_sio_seq=exp_sio_seq)

    # Relationship between stmt_b and stmt_c ---------------------------------------

    exp_sio_seq = _isl_map_with_marked_dims(
        "[pi, pj] -> {{ "
        "[{0}'=0, i', j'] -> [{0}=1, i, j] : "
        "0 <= i,i' < pi and 0 <= j,j' < pj and i > i'; "
        "[{0}'=0, i', j'] -> [{0}=1, i=i', j] : "
        "0 <= i' < pi and 0 <= j,j' < pj and j >= j'; "
        "}}".format(STATEMENT_VAR_NAME)
        )

    _check_sio_for_stmt_pair("stmt_b", "stmt_c", scheds, exp_sio_seq=exp_sio_seq)

    # Relationship between stmt_b and stmt_d ---------------------------------------

    exp_sio_seq = _isl_map_with_marked_dims(
        "[pt, pi, pj] -> {{ "
        "[{0}'=0, i', j'] -> [{0}=1, t] : "
        "0 <= i' < pi and 0 <= j' < pj and 0 <= t < pt "
        "}}".format(STATEMENT_VAR_NAME)
        )

    _check_sio_for_stmt_pair("stmt_b", "stmt_d", scheds, exp_sio_seq=exp_sio_seq)

    # Relationship between stmt_c and stmt_d ---------------------------------------

    exp_sio_seq = _isl_map_with_marked_dims(
        "[pt, pi, pj] -> {{ "
        "[{0}'=0, i', j'] -> [{0}=1, t] : "
        "0 <= i' < pi and 0 <= j' < pj and 0 <= t < pt "
        "}}".format(STATEMENT_VAR_NAME)
        )

    _check_sio_for_stmt_pair("stmt_c", "stmt_d", scheds, exp_sio_seq=exp_sio_seq)


def test_statement_instance_ordering_with_hw_par_tags():
    from loopy.schedule.checker import (
        get_schedules_for_statement_pairs,
    )
    from loopy.schedule.checker.utils import (
        partition_inames_by_concurrency,
    )

    # Example kernel
    knl = lp.make_kernel(
        [
            "{[i,ii]: 0<=i,ii<pi}",
            "{[j,jj]: 0<=j,jj<pj}",
        ],
        """
        for i
            for ii
                for j
                    for jj
                        <>temp = b[i,ii,j,jj]  {id=stmt_a}
                        a[i,ii,j,jj] = temp + 1  {id=stmt_b,dep=stmt_a}
                    end
                end
            end
        end
        """,
        name="example",
        assumptions="pi,pj >= 1",
        lang_version=(2018, 2)
        )
    knl = lp.add_and_infer_dtypes(knl, {"a": np.float32, "b": np.float32})
    knl = lp.tag_inames(knl, {"j": "l.1", "jj": "l.0", "i": "g.0"})

    # Get a linearization
    proc_knl = preprocess_kernel(knl)
    lin_knl = get_one_linearized_kernel(proc_knl)
    linearization_items = lin_knl.linearization

    # Get pairwise schedules
    stmt_id_pairs = [
        ("stmt_a", "stmt_b"),
        ]
    scheds = get_schedules_for_statement_pairs(
        lin_knl,
        linearization_items,
        stmt_id_pairs,
        return_schedules=True,
        )

    # Create string for representing parallel iname condition in sio
    conc_inames, _ = partition_inames_by_concurrency(knl)
    par_iname_condition = " and ".join(
        "{0} = {0}'".format(iname) for iname in conc_inames)

    # Relationship between stmt_a and stmt_b ---------------------------------------

    exp_sio_seq = _isl_map_with_marked_dims(
        "[pi, pj] -> {{ "
        "[{0}'=0, i', ii', j', jj'] -> [{0}=1, i, ii, j, jj] : "
        "0 <= i,ii,i',ii' < pi and 0 <= j,jj,j',jj' < pj and ii >= ii' "
        "and {1} "
        "}}".format(
            STATEMENT_VAR_NAME,
            par_iname_condition,
            )
        )

    _check_sio_for_stmt_pair("stmt_a", "stmt_b", scheds, exp_sio_seq=exp_sio_seq)

    # ------------------------------------------------------------------------------

# }}}


# {{{ SIOs and schedules with barriers

def test_sios_and_schedules_with_lbarriers():
    from loopy.schedule.checker import (
        get_schedules_for_statement_pairs,
    )

    knl = lp.make_kernel(
        [
            #"{[i,j,l0,l1,g0]: 0<=i,j,l0,l1,g0<p}",
            "{[i,j]: 0<=i,j<p1}",
            "{[l0,l1,g0]: 0<=l0,l1,g0<p2}",
        ],
        """
        for g0
            for l0
                for l1
                    <>temp0 = 0  {id=0}
                    ... lbarrier  {id=b0,dep=0}
                    <>temp1 = 1  {id=1,dep=b0}
                    for i
                        <>tempi0 = 0  {id=i0,dep=1}
                        ... lbarrier {id=ib0,dep=i0}
                        <>tempi1 = 0  {id=i1,dep=ib0}
                        <>tempi2 = 0  {id=i2,dep=i1}
                        for j
                            <>tempj0 = 0  {id=j0,dep=i2}
                            ... lbarrier {id=jb0,dep=j0}
                            <>tempj1 = 0  {id=j1,dep=jb0}
                        end
                    end
                    <>temp2 = 0  {id=2,dep=i0}
                end
            end
        end
        """,
        name="funky",
        assumptions="p1,p2 >= 1",
        lang_version=(2018, 2)
        )
    knl = lp.tag_inames(knl, {"l0": "l.0", "l1": "l.1", "g0": "g.0"})

    # Get a linearization
    proc_knl = preprocess_kernel(knl)
    lin_knl = get_one_linearized_kernel(proc_knl)
    linearization_items = lin_knl.linearization

    insn_id_pairs = [("j1", "2"), ("1", "i0")]
    scheds = get_schedules_for_statement_pairs(
        lin_knl, linearization_items, insn_id_pairs, return_schedules=True)

    # Relationship between j1 and 2 --------------------------------------------

    # Get maps (include schedules, just for test purposes)
    (
        sio_seq, (sched_map_before, sched_map_after)
    ), (
        sio_lconc, (lconc_sched_before, lconc_sched_after)
    ), (
        sio_gconc, (gconc_sched_before, gconc_sched_after)
    ) = scheds[("j1", "2")]

    # Create expected maps and compare

    conc_iname_bound_str = "0<=l0,l1,g0<p2"
    conc_iname_bound_str_p = "0<=l0',l1',g0'<p2"

    lconc_sched_before_exp = isl.Map(
        "[p1,p2] -> {[%s=0,i,j,l0,l1,g0] -> [%s] : 0<=i,j<p1 and %s}"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string(
                ["2", "i", "2", "j", "1"],
                lid_inames=["l0", "l1"], gid_inames=["g0"],
                prefix=BLEX_VAR_PREFIX,
                ),
            conc_iname_bound_str,
            )
        )

    lconc_sched_after_exp = isl.Map(
        "[p2] -> {[%s=1,l0,l1,g0] -> [%s] : %s}"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string(
                ["3", "0", "0", "0", "0"],
                lid_inames=["l0", "l1"], gid_inames=["g0"],
                prefix=BLEX_VAR_PREFIX,
                ),
            conc_iname_bound_str,
            )
        )

    sio_lconc_exp = _isl_map_with_marked_dims(
        "[p1,p2] -> {{ "
        "[{0}'=0,i',j',l0',l1',g0'] -> [{0}=1,l0,l1,g0] : "
        "((0 <= i' < p1 and 0 <= j' < p1-1) or "  # not last iteration of j
        " (0 <= i' < p1-1 and 0 <= j' < p1))"  # not last iteration of i
        "and g0 = g0' "  # within a single group
        "and {1} and {2}"  # conc iname bounds
        "}}".format(
            STATEMENT_VAR_NAME,
            conc_iname_bound_str,
            conc_iname_bound_str_p,
            )
        )

    _align_and_compare_maps(
        [lconc_sched_before_exp, lconc_sched_after_exp, sio_lconc_exp],
        [lconc_sched_before, lconc_sched_after, sio_lconc],
        )

    # Check for some key example pairs in the sio_lconc map

    # As long as this is not the last iteration of the i loop, then there
    # should be a barrier between the last instance of statement j1
    # and statement 2:
    p1_val = 7
    last_i_val = p1_val - 1
    max_non_last_i_val = last_i_val - 1  # max i val that isn't the last iteration

    wanted_pairs = _isl_map_with_marked_dims(
        "[p1,p2] -> {{"
        "[{0}' = 0, i', j'=p1-1, g0', l0', l1'] -> [{0} = 1, l0, l1, g0] : "
        "0 <= i' <= {1} and "  # constrain i
        "p1 >= {2} and "  # constrain p
        "0<=l0',l1',g0',l0,l1,g0<p2 and g0=g0'"
        "}}".format(STATEMENT_VAR_NAME, max_non_last_i_val, p1_val))
    wanted_pairs = ensure_dim_names_match_and_align(wanted_pairs, sio_lconc)

    assert wanted_pairs.is_subset(sio_lconc)

    # If this IS the last iteration of the i loop, then there
    # should NOT be a barrier between the last instance of statement j1
    # and statement 2:
    unwanted_pairs = _isl_map_with_marked_dims(
        "[p1,p2] -> {{"
        "[{0}' = 0, i', j'=p1-1, g0', l0', l1'] -> [{0} = 1, l0, l1, g0] : "
        "0 <= i' <= {1} and "  # constrain i
        "p1 >= {2} and "  # constrain p
        "0<=l0',l1',g0',l0,l1,g0<p2 and g0=g0'"
        "}}".format(STATEMENT_VAR_NAME, last_i_val, p1_val))
    unwanted_pairs = ensure_dim_names_match_and_align(unwanted_pairs, sio_lconc)

    assert not unwanted_pairs.is_subset(sio_lconc)

    # Relationship between 1 and i0 --------------------------------------------

    # Get maps (include schedules, just for test purposes)
    (
        sio_seq, (sched_map_before, sched_map_after)
    ), (
        sio_lconc, (lconc_sched_before, lconc_sched_after)
    ), (
        sio_gconc, (gconc_sched_before, gconc_sched_after)
    ) = scheds[("1", "i0")]

    # Create expected maps and compare

    lconc_sched_before_exp = isl.Map(
        "[p2] -> {[%s=0,l0,l1,g0] -> [%s] : 0<=l0,l1,g0<p2}"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string(
                ["1", "0", "0", "0", "0"],
                lid_inames=["l0", "l1"], gid_inames=["g0"],
                prefix=BLEX_VAR_PREFIX,
                ),
            )
        )

    lconc_sched_after_exp = isl.Map(
        "[p1,p2] -> {[%s=1,i,j,l0,l1,g0] -> [%s] : 0<=i,j<p1 and 0<=l0,l1,g0<p2}"
        % (
            STATEMENT_VAR_NAME,
            _lex_point_string(
                ["2", "i", "0", "0", "0"],
                lid_inames=["l0", "l1"], gid_inames=["g0"],
                prefix=BLEX_VAR_PREFIX,
                ),
            )
        )

    sio_lconc_exp = _isl_map_with_marked_dims(
        "[p1,p2] -> {{ "
        "[{0}'=0,l0',l1',g0'] -> [{0}=1,i,j,l0,l1,g0] : "
        "1 <= i < p1 and 0 <= j < p1 "  # not first iteration of i
        "and g0 = g0' "  # within a single group
        "and {1} and {2}"  # conc iname bounds
        "}}".format(
            STATEMENT_VAR_NAME,
            conc_iname_bound_str,
            conc_iname_bound_str_p,
            )
        )

    _align_and_compare_maps(
        [lconc_sched_before_exp, lconc_sched_after_exp, sio_lconc_exp],
        [lconc_sched_before, lconc_sched_after, sio_lconc],
        )
# }}}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        exec(sys.argv[1])
    else:
        from pytest import main
        main([__file__])

# vim: foldmethod=marker
