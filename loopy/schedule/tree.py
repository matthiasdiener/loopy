import pymbolic.primitives as prim
import loopy.schedule as schedule
import islpy as isl
from typing import List, Union, Any, Optional, Tuple
from dataclasses import dataclass, field
from functools import reduce
from islpy import dim_type
from loopy.diagnostic import LoopyError
from loopy.kernel import KernelState
from loopy.kernel.tools import get_all_inames_tagged_with


# {{{ LoopKernel.schedule a tree

class ScheduleNode:
    """
    Abstract class for a schedule node in a class:`~loopy.LoopKernel`.
    """
    pass


@dataclass
class RunInstruction(ScheduleNode):
    insn_id: str

    mapper_method: str = field(default="map_run_instruction", repr=False, init=False)


@dataclass
class Barrier(ScheduleNode):
    """
    .. attribute:: comment

        A plain-text comment explaining why the barrier was inserted.

    .. attribute:: synchronization_kind

        ``"local"`` or ``"global"``

    .. attribute:: mem_kind

        ``"local"`` or ``"global"``

    .. attribute:: originating_insn_id
    """
    comment: str
    synchronization_kind: str
    mem_kind: str
    originating_insn_id: Optional[str]

    mapper_method: str = field(default="map_barrier", repr=False, init=False)


@dataclass
class InstructionBlock(ScheduleNode):
    """
    List of instruction ids that are to be executed in sequence. An instruction
    block cannot contain other blocks or loops.

    .. attribute:: children
    """
    children: List[Union[RunInstruction]]

    mapper_method: str = field(default="map_instruction_block", repr=False,
                               init=False)


@dataclass
class Loop(ScheduleNode):
    """
    A loop with the induction variable *iname*.
    """
    iname: str
    children: List[Union[InstructionBlock, "Loop", Barrier]]

    mapper_method: str = field(default="map_loop", repr=False, init=False)

    def with_children(self, children):
        """
        Returns a copy of *self* with *children* as its new children.
        """
        return Loop(self.iname, children)


@dataclass
class Function(ScheduleNode):
    """
    A function definition.

    .. attribute:: name

        An instance of :class:`str`

    .. attribute:: extra_args

    .. attribute:: extra_inames
    """
    name: str
    extra_args: List[Any]
    extra_inames: List[str]
    children: List[Union[InstructionBlock, Loop, Barrier]]

    mapper_method: str = field(default="map_function", repr=False, init=False)

    def with_children(self, children):
        """
        Returns a copy of *self* with *children* as its new children.
        """
        return Function(self.name, self.extra_args, self.extra_inames, children)


@dataclass
class PolyhedralLoop(Loop):
    iname: str
    domain: isl.BasicSet
    children: List[Union[InstructionBlock, Loop, "If", Barrier]]

    mapper_method: str = field(default="map_polyhedral_loop", repr=False, init=False)


@dataclass
class For(Loop):
    iname: str
    lower_bound: Union[int, prim.Expression]
    upper_bound: Union[int, prim.Expression]
    step: int
    children: List[Union[InstructionBlock, Loop, "If", Barrier]]

    mapper_method: str = field(default="map_for", repr=False, init=False)


@dataclass
class If(ScheduleNode):
    condition: Union[int, bool, prim.Expression]
    children: List[Union[Loop, InstructionBlock, Function]]

    mapper_method: str = field(default="map_if", repr=False, init=False)


@dataclass
class Schedule(ScheduleNode):
    """
    Top-level schedule description.
    """
    children: List[Union[Loop, InstructionBlock, Function, Barrier]]

    mapper_method: str = field(default="map_schedule", repr=False, init=False)

    def with_children(self, children):
        """
        Returns a copy of *self* with *children* as its new children.
        """
        return Schedule(children)


@dataclass
class ScheduleTreeBuilder:
    """
    A builder for :class:`Schedule`.
    """

    schedule: Schedule
    _build_stack: List[ScheduleNode]

    @staticmethod
    def new():
        sched = Schedule([])
        return ScheduleTreeBuilder(sched, [sched])

    @property
    def current_node(self):
        return self._build_stack[-1]

    def make_current_node(self, node):
        self._build_stack.append(node)

    def make_and_enter_function(self, name, extra_args, extra_inames):
        if isinstance(self.current_node, InstructionBlock):
            # end of instruction block
            self._build_stack.pop()

        assert isinstance(self.current_node, Schedule)
        new_function = Function(name, extra_args, extra_inames, [])
        self.current_node.children.append(new_function)
        self.make_current_node(new_function)

    def make_and_enter_instruction_block(self):
        assert isinstance(self.current_node, (Function, Loop, Schedule))
        new_block = InstructionBlock([])
        self.current_node.children.append(new_block)
        self.make_current_node(new_block)

    def make_and_enter_loop(self, iname):
        if isinstance(self.current_node, InstructionBlock):
            # end of instruction block
            self._build_stack.pop()

        assert isinstance(self.current_node, (Schedule, Function, Loop))
        new_loop = Loop(iname, [])
        self.current_node.children.append(new_loop)
        self.make_current_node(new_loop)

    def add_run_instruction(self, insn_id):
        if not isinstance(self.current_node, InstructionBlock):
            self.make_and_enter_instruction_block()

        self.current_node.children.append(RunInstruction(insn_id))

    def add_barrier(self, comment, sync_kind, mem_kind, insn_id):
        if isinstance(self.current_node, InstructionBlock):
            self._build_stack.pop()

        assert isinstance(self.current_node, (Schedule, Function, Loop))
        self.current_node.children.append(Barrier(comment, sync_kind, mem_kind,
                                                  insn_id))

    def exit_function(self):
        if isinstance(self.current_node, InstructionBlock):
            self._build_stack.pop()

        assert isinstance(self.current_node, Function)
        return self._build_stack.pop()

    def exit_loop(self):
        if isinstance(self.current_node, InstructionBlock):
            self._build_stack.pop()
        assert isinstance(self.current_node, Loop)
        return self._build_stack.pop()

    def exit(self):
        if isinstance(self.current_node, InstructionBlock):
            self._build_stack.pop()
        assert isinstance(self.current_node, Schedule)
        return self._build_stack.pop()


def make_schedule_tree(kernel):
    # bob: the schedule builder
    bob = ScheduleTreeBuilder.new()

    for sched_item in kernel.schedule:
        if isinstance(sched_item, schedule.CallKernel):
            bob.make_and_enter_function(sched_item.kernel_name,
                                        sched_item.extra_args,
                                        sched_item.extra_inames)
        elif isinstance(sched_item, schedule.ReturnFromKernel):
            fn = bob.exit_function()
            assert fn.name == sched_item.kernel_name
        elif isinstance(sched_item, schedule.EnterLoop):
            bob.make_and_enter_loop(sched_item.iname)
        elif isinstance(sched_item, schedule.LeaveLoop):
            loop = bob.exit_loop()
            assert loop.iname == sched_item.iname
        elif isinstance(sched_item, schedule.RunInstruction):
            bob.add_run_instruction(sched_item.insn_id)
        elif isinstance(sched_item, schedule.Barrier):
            bob.add_barrier(sched_item.comment,
                            sched_item.synchronization_kind,
                            sched_item.mem_kind,
                            sched_item.originating_insn_id)
        else:
            raise NotImplementedError(type(sched_item))

    kernel = kernel.copy(schedule=bob.exit())
    return kernel

# }}}


@dataclass
class GroupedChildren:
    contents: List[ScheduleNode]


class Mapper:
    def __call__(self, expr, *args, **kwargs):
        try:
            method = getattr(self, expr.mapper_method)
        except AttributeError:
            raise LoopyError(f"{type(self)} cannot handle expressions of"
                             f" type {type(expr)}.")

        return method(expr, *args, **kwargs)

    rec = __call__


class IdentityMapper(Mapper):
    def combine(self, values):
        result = []
        for val in values:
            if isinstance(val, GroupedChildren):
                result.extend(val.contents)
            else:
                assert isinstance(val, ScheduleNode)
                result.append(val)

        return result

    def map_schedule(self, expr, *args, **kwargs):
        return Schedule(self.combine([self.rec(child, *args, **kwargs)
                                      for child in expr.children]))

    def map_instruction_block(self, expr, *args, **kwargs):
        return InstructionBlock(self.combine([self.rec(child, *args, **kwargs)
                                              for child in expr.children]))

    def map_function(self, expr, *args, **kwargs):
        return Function(expr.name,
                        expr.extra_args,
                        expr.extra_inames,
                        self.combine([self.rec(child, *args, **kwargs)
                                      for child in expr.children]))

    def map_loop(self, expr, *args, **kwargs):
        return Loop(expr.iname,
                    self.combine([self.rec(child, *args, **kwargs)
                                  for child in expr.children]))

    def map_barrier(self, expr, *args, **kwargs):
        return Barrier(expr.comment, expr.synchronization_kind,
                       expr.mem_kind, expr.originating_insn_id)

    def map_run_instruction(self, expr, *args, **kwargs):
        return RunInstruction(expr.insn_id)


class CombineMapper(Mapper):
    def combine(self, values):
        raise NotImplementedError

    def map_schedule(self, expr, *args, **kwargs):
        return self.combine([self.rec(child, *args, **kwargs)
                             for child in expr.children])

    def map_instruction_block(self, expr, *args, **kwargs):
        return self.combine([self.rec(child, *args, **kwargs)
                             for child in expr.children])

    def map_function(self, expr, *args, **kwargs):
        return self.combine([self.rec(child, *args, **kwargs)
                             for child in expr.children])

    def map_loop(self, expr, *args, **kwargs):
        return self.combine([self.rec(child, *args, **kwargs)
                             for child in expr.children])

    def map_polyhedral_loop(self, expr, *args, **kwargs):
        return self.combine([self.rec(child, *args, **kwargs)
                             for child in expr.children])

    def map_for(self, expr, *args, **kwargs):
        return self.combine([self.rec(child, *args, **kwargs)
                             for child in expr.children])

    def map_if(self, expr, *args, **kwargs):
        return self.combine([self.rec(child, *args, **kwargs)
                             for child in expr.children])

    def map_barrier(self, expr, *args, **kwargs):
        raise NotImplementedError

    def map_run_instruction(self, expr, *args, **kwargs):
        raise NotImplementedError


class StringifyMapper(CombineMapper):
    SHIFTWIDTH = 2

    def __init__(self, kernel):
        self.kernel = kernel

    def combine(self, values):
        return "\n".join(values)

    def _indent(self, level):
        return level*self.SHIFTWIDTH*" "

    def map_function(self, expr, level=0):
        return self.combine([(f"{self._indent(level)}CALL KERNEL {expr.name}("
                              f"extra_args={expr.extra_args}, "
                              f"extra_inames={expr.extra_inames})"),
                             super().map_function(expr, level+1),
                             f"{self._indent(level)}RETURN FROM KERNEL {expr.name}"])

    def map_run_instruction(self, expr, level=0):
        from loopy.schedule import format_insn
        return (f"{self._indent(level)}"
                f"{format_insn(self.kernel, expr.insn_id)}")

    def map_barrier(self, expr, level=0):
        return (f"{self._indent(level)}... {expr.kind[0]}barrier")

    def map_loop(self, expr, level=0):
        return self.combine([f"{self._indent(level)}for {expr.iname}",
                             super().map_loop(expr, level+1),
                             f"{self._indent(level)}end {expr.iname}"])

    def map_polyhedral_loop(self, expr, level=0):
        return self.combine([f"{self._indent(level)}PolyhedralFor({expr.domain})",
                             super().map_polyhedral_loop(expr, level+1),
                             f"{self._indent(level)}end {expr.iname}"])

    def map_for(self, expr, level=0):
        return self.combine([f"{self._indent(level)}For({expr.iname}, "
                             f"{expr.lower_bound}, {expr.upper_bound}, "
                             f"{expr.step})",
                             super().map_for(expr, level+1),
                             f"{self._indent(level)}end {expr.iname}"])

    def map_if(self, expr, level=0):
        return self.combine([f"{self._indent(level)}If({expr.condition})",
                             super().map_if(expr, level+1),
                             f"{self._indent(level)}Endif"])


def _align_and_intersect(d1, d2):
    d1, d2 = isl.align_two(d1, d2)
    return d1 & d2


def _align_and_gist(d1, d2):
    d1, d2 = isl.align_two(d1, d2)
    return d1.gist(d2)


def _wrap_in_if(cond, nodes):
    from loopy.symbolic import set_to_cond_expr
    if cond.is_universe():
        return nodes
    else:
        return [If(set_to_cond_expr(cond), nodes)]


def _implement_hw_axes_in_domains(implemented_domain, domain,
                                  kernel, gsize, lsize):
    """
    If *domain* contains any inames going along hardware inames account for
    those in *implemented_domain*.

    :arg gsize: A tuple of :class:`isl.PwAff` denoting the size of the

    :returns: An instance of :class:`isl.BasicSet` that includes constraints
        from *implemented_domain* and constraints arising from constraining
        hardware inames in *domain* to their corresponding
    """
    from loopy.kernel.data import AxisTag, GroupIndexTag, LocalIndexTag
    from loopy.isl_helpers import make_slab, static_min_of_pw_aff

    all_hw_inames = get_all_inames_tagged_with(kernel, AxisTag)

    for dim_name in domain.get_var_dict():
        if dim_name in all_hw_inames:
            if dim_name in implemented_domain.get_var_dict():
                # this hardware dim is already implemented => ignore
                continue

            tag, = kernel.iname_tags_of_type(dim_name, AxisTag)
            assert isinstance(tag, (GroupIndexTag, LocalIndexTag))

            lbound = static_min_of_pw_aff(kernel
                                          .get_iname_bounds(dim_name)
                                          .lower_bound_pw_aff, constants_only=False)
            size = (gsize[tag.axis]

                    if isinstance(tag, GroupIndexTag)

                    else

                    lsize[tag.axis])

            if not isinstance(size, int):
                lbound, size = isl.align_two(lbound, size)

            implemented_domain = (implemented_domain
                                  .add_dims(dim_type.param, 1)
                                  .set_dim_name(dim_type.param,
                                                implemented_domain
                                                .dim(dim_type.param),
                                                dim_name))

            implemented_domain = (implemented_domain
                                  & make_slab(implemented_domain.space, dim_name,
                                              lbound, lbound + size))

    return implemented_domain.params()


@dataclass(frozen=True)
class PolyhedronLoopifierContext:
    implemented_domain: isl.BasicSet
    gsize: Optional[Tuple[prim.Expression, ...]] = None
    lsize: Optional[Tuple[prim.Expression, ...]] = None

    def copy(self, *, implemented_domain=None, gsize=None, lsize=None):
        if implemented_domain is None:
            implemented_domain = self.implemented_domain

        if gsize is None:
            gsize = self.gsize

        if lsize is None:
            lsize = self.lsize

        return PolyhedronLoopifierContext(implemented_domain, gsize, lsize)


class PolyhedronLoopifier(IdentityMapper):
    def __init__(self, kernel):
        self.kernel = kernel

    def map_schedule(self, expr):
        impl_domain = self.kernel.assumptions
        return super().map_schedule(expr,
                                    PolyhedronLoopifierContext(impl_domain))

    def map_function(self, expr, context):
        # get the implemented domain for the insn ids in this kernel
        # Shouldn't be difficult to write a combine mapper for it.
        gsize, lsize = self.kernel.get_grid_sizes_for_insn_ids_as_exprs(
            InstructionGatherer()(expr))
        # FIXME: Somehow we need to get rid of allowing the hardware inames to
        # be slabbed.
        return super().map_function(expr, context.copy(gsize=gsize,
                                                       lsize=lsize))

    def map_loop(self, expr, context):
        implemented_domain = context.implemented_domain
        assert implemented_domain.dim(dim_type.set) == 0

        domain = self.kernel.get_inames_domain(expr.iname)

        implemented_domain = _implement_hw_axes_in_domains(implemented_domain,
                                                           domain,
                                                           self.kernel,
                                                           context.gsize,
                                                           context.lsize)

        # {{{ make already implemented loops as parameters; project out inner loops

        for set_dim in domain.get_var_names(dim_type.set):
            dt, pos = domain.get_var_dict()[set_dim]
            assert dt == dim_type.set

            if set_dim in implemented_domain.get_var_dict():
                # make outer loop's iname a param
                domain = domain.move_dims(dim_type.param,
                                          domain.dim(dim_type.param),
                                          dt, pos, 1)
            elif set_dim != expr.iname:
                domain = domain.project_out(dt, pos, 1)
            else:
                pass

        # }}}

        assert domain.dim(dim_type.set) == 1

        domain = _align_and_gist(domain, implemented_domain)

        downstream_domain = _align_and_intersect(domain
                                                 .move_dims(dim_type.param,
                                                            domain.dim(dim_type
                                                                       .param),
                                                            dim_type.set,
                                                            0, 1),
                                                 implemented_domain
                                                 ).params()
        children = [self.rec(child, (context
                                     .copy(implemented_domain=downstream_domain)))
                    for child in expr.children]

        return PolyhedralLoop(iname=expr.iname,
                              children=self.combine(children),
                              domain=domain)

    def map_polyhedral_loop(self, expr, context):
        assert expr.domain.dim(dim_type.set) == 1
        assert context.implemented_domain.dim(dim_type.set) == 0

        domain = _align_and_gist(expr.domain, context.implemented_domain)
        downstream_domain = _align_and_intersect(domain,
                                                 context.implemented_domain)
        downstream_domain = downstream_domain.move_dims(dim_type.param,
                                                        (downstream_domain
                                                         .dim(dim_type.param)),
                                                        dim_type.set, 0, 1).params()
        children = [self.rec(child, (context
                                     .copy(implemented_domain=downstream_domain)))
                    for child in expr.children]

        return PolyhedralLoop(iname=expr.iname,
                              children=self.combine(children), domain=domain)


class UnvectorizableInamesCollector(CombineMapper):
    """
    Mapper to gather all insn ids.
    """
    def __init__(self, kernel):
        self.kernel = kernel

    def combine(self, values):
        assert all(isinstance(value, frozenset) for value in values)
        return reduce(frozenset.union, values, frozenset())

    def map_polyhedral_loop(self, expr):
        from loopy.kernel.data import VectorizeTag
        from loopy.isl_helpers import static_max_of_pw_aff, static_value_of_pw_aff
        from loopy.diagnostic import warn
        from loopy.symbolic import pw_aff_to_expr
        from loopy.expression import VectorizabilityChecker
        from loopy.codegen import Unvectorizable
        from loopy.kernel.instruction import MultiAssignmentBase

        if self.kernel.iname_tags_of_type(expr.iname, VectorizeTag):
            # FIXME: also assert that all children are just instruction blocks..
            bounds = self.kernel.get_iname_bounds(expr.iname, constants_only=True)

            length_aff = static_max_of_pw_aff(bounds.size, constants_only=True)

            if not length_aff.is_cst():
                warn(self.kernel, "vec_upper_not_const",
                     f"upper bound for vectorized loop '{expr.iname}' is not"
                     " a constant, cannot vectorize.")
                return frozenset([expr.iname])

            length = int(pw_aff_to_expr(length_aff))

            lower_bound_aff = static_value_of_pw_aff(bounds
                                                     .lower_bound_pw_aff
                                                     .coalesce(),
                                                     constants_only=False)

            if not lower_bound_aff.plain_is_zero():
                warn(self.kernel, "vec_lower_not_0",
                     f"lower bound for vectorized loop '{expr.iname}' is not zero,"
                     "cannot vectorize.")
                return frozenset([expr.iname])

            # {{{ validate the vectorizability of instructions within the

            for child in expr.children:
                if not isinstance(child, InstructionBlock):
                    warn(self.kernel, "vec_loop_complex_control_flow",
                         f"loop nest of vectorized loop '{expr.iname}' contains"
                         " other loops or barriers => unvectorizable.")
                    return frozenset([expr.iname])

                assert isinstance(child, InstructionBlock)
                for run_insn in child.children:
                    insn = self.kernel.id_to_insn[run_insn.insn_id]

                    if not isinstance(insn, MultiAssignmentBase):
                        warn(self.kernel, "vec_loop_contains_non_assignment_insn",
                             f"loop nest of vectorized loop '{expr.iname}' contains"
                             f" instruction of type {type(insn)} that cannot be"
                             " vectorized.")
                        return frozenset([expr.iname])

                    if insn.predicates:
                        warn(self.kernel, "vec_loop_contains_predicates",
                             f"loop nest of vectorized loop '{expr.iname}' contains"
                             "predicates => masking instances of vectorized"
                             "loop not yet supported.")
                        return frozenset([expr.iname])

                    if insn.atomicity:
                        warn(self.kernel, "vec_loop_contains_atomic_insns",
                             f"loop nest of vectorized loop '{expr.iname}' contains"
                             "atomic instructions => unvectorizable.")
                        return frozenset([expr.iname])

                    vcheck = VectorizabilityChecker(
                            self.kernel, expr.iname, length)

                    try:
                        lhs_is_vector = vcheck(insn.assignee)
                        rhs_is_vector = vcheck(insn.expression)
                    except Unvectorizable as e:
                        warn(self.kernel, "vectorize_failed",
                             f"Vectorization of '{expr.iname}' failed due to '{e}'"
                             f" in '{insn.id}'.")
                        return frozenset([expr.iname])
                    else:
                        if not lhs_is_vector and rhs_is_vector:
                            warn(self.kernel, "vectorize_failed",
                                 f"Vectorization of '{expr.iname}' failed in'"
                                 f" '{insn.id}' as LHS is scalar, RHS is vector,"
                                 " cannot assign")
                            return frozenset([expr.iname])

            # }}}

        return super().map_polyhedral_loop(expr)

    def map_run_instruction(self, expr):
        return frozenset()

    def map_barrier(self, expr):
        return frozenset()


class Unroller(PolyhedronLoopifier):
    """
    .. attribute extra_unroll_inames::

        A :class:`frozenset` of inames that are to be unrolled other than the
        usual suspects tagged with 'unr`. One use-case could be unrolling could
        be a fallback implementation for other iname implementations.
    """
    def __init__(self, kernel, extra_unroll_inames):
        super().__init__(kernel)
        self.extra_unroll_inames = extra_unroll_inames

    def map_polyhedral_loop(self, expr, context):
        from loopy.kernel.data import UnrollTag, UnrolledIlpTag
        from loopy.isl_helpers import (make_slab, static_max_of_pw_aff,
                                       static_min_of_pw_aff)

        if (self.kernel.iname_tags_of_type(expr.iname, (UnrolledIlpTag,
                                                        UnrollTag))
                or expr.iname in self.extra_unroll_inames):
            domain = _align_and_gist(expr.domain, context.implemented_domain)
            ubound = static_max_of_pw_aff(domain.dim_max(0), constants_only=False)
            lbound = static_min_of_pw_aff(domain.dim_min(0), constants_only=False)
            # FIXME: Write a better error message o'er here that the loop
            # cannot be unrolled.
            size = static_max_of_pw_aff(ubound-lbound+1, constants_only=True)
            assert size.is_cst()

            result = []
            for i in range(size.get_constant_val().to_python()):
                unrll_dom = make_slab(domain.space, expr.iname, lbound+i,
                                      lbound+i+1) & domain
                if unrll_dom.is_empty():
                    continue

                dwnstrm_dom = _align_and_intersect(unrll_dom,
                                                   context.implemented_domain)

                dwnstrm_dom = dwnstrm_dom.move_dims(dim_type.param,
                                                    (dwnstrm_dom
                                                     .dim(dim_type.param)),
                                                    dim_type.set, 0, 1).params()
                children = [self.rec(child, (context
                                            .copy(implemented_domain=dwnstrm_dom)))
                            for child in expr.children]

                result.append(PolyhedralLoop(iname=expr.iname,
                                             children=self.combine(children),
                                             domain=unrll_dom))

            return GroupedChildren(contents=result)
        else:
            return super().map_polyhedral_loop(expr, context)

    def map_loop(self, expr, context):
        raise RuntimeError("At this point, all loops should have resolved as"
                           " polyhedral loops.")


class PredicateInsertionMapper(PolyhedronLoopifier):
    def map_instruction_block(self, expr, context):
        from loopy.symbolic import set_to_cond_expr

        assert len({self.kernel.id_to_insn[child.insn_id].within_inames
                    for child in expr.children}) == 1
        assert len({self.kernel.id_to_insn[child.insn_id].predicates
                    for child in expr.children}) == 1

        inames, = {self.kernel.id_to_insn[child.insn_id].within_inames
                    for child in expr.children}
        predicates, = {self.kernel.id_to_insn[child.insn_id].predicates
                        for child in expr.children}

        # {{{ compute the predicates due to the hardware inames

        from loopy.kernel.data import AxisTag
        hw_inames = inames & get_all_inames_tagged_with(self.kernel, AxisTag)

        if hw_inames:

            impl_domain = context.implemented_domain
            domain = (self.kernel.get_inames_domain(hw_inames)
                        .project_out_except(types=[dim_type.set],
                                            names=hw_inames))
            impl_domain = _implement_hw_axes_in_domains(impl_domain,
                                                        domain,
                                                        self.kernel,
                                                        context.gsize,
                                                        context.lsize)
            domain = (domain
                        .move_dims(dim_type.param, domain.dim(dim_type.param),
                                    dim_type.set, 0, domain.dim(dim_type.set)))
            unimplemented_domain = (isl.align_spaces(domain, impl_domain)
                                    .gist(impl_domain))

            if not unimplemented_domain.is_universe():
                predicates |= {set_to_cond_expr(unimplemented_domain)}

        # }}}

        new_insn_block = InstructionBlock([self.rec(child, context)
                                            for child in expr.children])

        if predicates:
            from pymbolic.primitives import LogicalAnd
            return If(LogicalAnd(tuple(predicates)), [new_insn_block])
        else:
            return new_insn_block

    def map_polyhedral_loop(self, expr, context):
        from loopy.symbolic import pw_aff_to_expr
        from loopy.isl_helpers import (static_min_of_pw_aff,
                                       static_max_of_pw_aff, make_slab)

        assert expr.domain.dim(dim_type.set) == 1
        lb = static_min_of_pw_aff(expr.domain.dim_min(0).gist(context
                                                              .implemented_domain),
                                  constants_only=False)
        ub = static_max_of_pw_aff(expr.domain.dim_max(0).gist(context
                                                              .implemented_domain),
                                  constants_only=False)
        set_implemented_in_loop = make_slab(expr.domain.space, expr.iname, lb, ub+1)

        outer_condition = _align_and_gist(expr.domain.project_out(dim_type.set,
                                                                  0, 1),
                                          set_implemented_in_loop)
        inner_condition = _align_and_gist(expr.domain.affine_hull(),
                                          set_implemented_in_loop)

        step = 1  # TODO: from inner_condition try to guess the step

        for_ = For(iname=expr.iname,
                   lower_bound=pw_aff_to_expr(lb),
                   upper_bound=pw_aff_to_expr(ub),
                   step=step,
                   children=_wrap_in_if(inner_condition,
                                        (super()
                                         .map_polyhedral_loop(expr, context)
                                         .children)))

        if outer_condition.is_universe():
            return for_
        else:
            from loopy.symbolic import set_to_cond_expr
            return If(set_to_cond_expr(outer_condition), [for_])


class InstructionGatherer(CombineMapper):
    """
    Mapper to gather all insn ids.
    """
    def combine(self, values):
        assert all(isinstance(value, frozenset) for value in values)
        return reduce(frozenset.union, values, frozenset())

    def map_run_instruction(self, expr):
        return frozenset([expr.insn_id])

    def map_barrier(self, expr):
        if expr.originating_insn_id is not None:
            return frozenset([expr.originating_insn_id])
        else:
            return frozenset()


class InstructionBlockHomogenizer(IdentityMapper):
    """
    Splits instruction blocks into multiple instruction blocks into multiple
    instruction blocks so that all the instruction blocks
    """
    def __init__(self, kernel):
        self.kernel = kernel

    def _are_insns_similar(self, insn1_id, insn2_id):
        insn1 = self.kernel.id_to_insn[insn1_id]
        insn2 = self.kernel.id_to_insn[insn2_id]
        return (insn1.within_inames == insn2.within_inames
                and insn1.predicates == insn2.predicates)

    def map_instruction_block(self, expr):
        insn_blocks = []

        similar_run_insns = []

        for run_insn in expr.children:
            if similar_run_insns:
                if self._are_insns_similar(similar_run_insns[-1].insn_id,
                                            run_insn.insn_id):
                    similar_run_insns.append(run_insn)
                else:
                    insn_blocks.append(InstructionBlock(similar_run_insns))
                    similar_run_insns = [run_insn]
            else:
                similar_run_insns.append(run_insn)

        insn_blocks.append(InstructionBlock(similar_run_insns))

        return GroupedChildren(insn_blocks)


def homogenize_instruction_blocks(kernel):
    """
    Returns a copy of *kernel* by splitting each instruction blocks in the
    kernel's schedule into multiple instruction blocks so that all the
    instructions in the updated instruction blocks have same predicates and
    within_inames.

    .. note::

        This might be a helpful transformation if the caller intends to operate
        on instruction blocks with the assumption that all instructions in an
        instruction block are homogenous under some criterion.
    """
    # TODO: Could be generalized by taking the homogenization criterion as an
    # argument.

    new_schedule = InstructionBlockHomogenizer(kernel)(kernel.schedule)
    return kernel.copy(schedule=new_schedule)


def insert_predicates_into_schedule(kernel):
    if kernel.iname_slab_increments:
        raise NotImplementedError

    assert kernel.state >= KernelState.LINEARIZED
    assert isinstance(kernel.schedule, Schedule)

    # {{{ preprocessing before beginning the predicate insertion.

    kernel = homogenize_instruction_blocks(kernel)

    # }}}

    schedule = PolyhedronLoopifier(kernel)(kernel.schedule)
    unvectorizable_inames = UnvectorizableInamesCollector(kernel)(schedule)
    # FIXME: (For now) unvectorizable inames always fallback to unrolling this
    # should be selected based on the target.
    schedule = Unroller(kernel, unvectorizable_inames)(schedule)
    schedule = PredicateInsertionMapper(kernel)(schedule)

    kernel = kernel.copy(schedule=schedule)

    return kernel.copy(schedule=schedule)


def get_insns_in_function(kernel, name):
    function, = [child for child in kernel.schedule.children
                 if isinstance(child, Function) and child.name == name]
    return InstructionGatherer()(function)