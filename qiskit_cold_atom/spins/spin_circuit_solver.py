# This code is part of Qiskit.
#
# (C) Copyright IBM 2021.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""Module to simulate spin circuits."""

import math
from typing import List, Union, Optional
from fractions import Fraction
import numpy as np
from scipy.sparse import csc_matrix
from qiskit import QuantumCircuit
from qiskit_nature.second_q.operators import SpinOp
from qiskit_cold_atom.base_circuit_solver import BaseCircuitSolver
from qiskit_cold_atom.exceptions import QiskitColdAtomError


class SpinCircuitSolver(BaseCircuitSolver):
    """Performs numerical simulations of spin systems by exactly computing the time
    evolution under unitary operations generated by exponentiating spin Hamiltonians."""

    def __init__(
        self,
        spin: Union[float, Fraction] = Fraction(1, 2),
        shots: Optional[int] = None,
        seed: Optional[int] = None,
    ):
        """
        Initialize a spin circuit solver.

        Args:
            spin: The length of the spin of each wire in the circuit.
            shots: Amount of shots for the measurement simulation;
                   if not None, measurements are performed.
            seed: The seed for the RNG for the measurement simulation.

        Raises:
            QiskitColdAtomError: if the spin is not a positive integer or half-integer.
        """

        self.spin = Fraction(spin)
        if self.spin.denominator not in (1, 2):
            raise QiskitColdAtomError(
                f"spin must be a positive half-integer (integer or half-odd-integer), "
                f"not {self.spin}."
            )

        super().__init__(shots=shots, seed=seed)

    def get_initial_state(self, circuit: QuantumCircuit) -> csc_matrix:
        """
        Return the initial state as a sparse column vector.

        Args:
            circuit: A circuit that tells us the dimension of the initial state to return.

        Returns:
            initial state: A sparse column vector of the initial state.
        """

        dim = int((2 * self.spin + 1) ** circuit.num_qubits)

        initial_state = csc_matrix(([1 + 0j], ([0], [0])), shape=(dim, 1), dtype=complex)
        return initial_state

    def _embed_operator(self, operator: SpinOp, num_wires: int, qargs: List[int]) -> SpinOp:
        """
        Turning a SpinOp operator that acts onto the wires given in qargs into an operator
        that acts on the entire register of the circuit by manipulating the indices of the
        sparse labels of the SpinOps.

        Args:
            operator: SpinOp describing the generating Hamiltonian of a gate
            num_wires: The total number of wires in which the operator should be embedded into
            qargs: The wire indices the gate acts on

        Returns:
            A SpinOp acting on the entire quantum register of the Circuit

        Raises:
            QiskitColdAtomError: - If the given operator is not a SpinOp
                                 - If the size of the operator does not match the given qargs
        """
        if not isinstance(operator, SpinOp):
            raise QiskitColdAtomError(f"Expected SpinOp; got {type(operator).__name__} instead")

        if operator.num_spins != len(qargs):
            raise QiskitColdAtomError(
                f"operator size {operator.num_spins} does not match qargs {qargs} of the gates."
            )

        embedded_op_dict = {}
        for label, factor in operator._data.items():
            old_labels = label.split()
            new_labels = [term[:2] + str(qargs[int(term[2])]) + term[3:] for term in old_labels]
            embedded_op_dict[" ".join(map(str, new_labels))] = factor

        return SpinOp(embedded_op_dict, spin=self.spin, num_spins=num_wires)

    def operator_to_mat(self, operator: SpinOp) -> csc_matrix:
        """
        Convert a SpinOp describing a gate generator to a sparse matrix.

        Args:
            operator: spin operator of which to compute the matrix representation

        Returns:
            scipy.sparse matrix of the Hamiltonian
        """
        return csc_matrix(operator.to_matrix())

    def preprocess_circuit(self, circuit: QuantumCircuit):
        r"""
        Compute the Hilbert space dimension of the given quantum circuit as :math:`(2S+1)^N`
        where :math:`S` is the length of the spin and :math:`N` is the number of spins in
        the quantum circuit.

        Args:
            circuit: The circuit to pre-process.
        """
        self._dim = int((2 * self.spin + 1) ** circuit.num_qubits)

    def draw_shots(self, measurement_distribution: List[float]) -> List[str]:
        r"""A helper function to draw counts from a given distribution of measurement outcomes.

        Args:
            measurement_distribution: List of probabilities of the individual measurement outcomes.

        Returns:
            outcome_memory: A list of individual measurement results, e.g. ["12 3 4", "0 4 9", ...]
            The outcome of each shot is denoted by a space-delimited string "a1 a2 a3 ..." where
            :math:`a_i` is the measured level of the spin with possible values ranging from 0 to 2S
            The :math:`a_i` are in reverse order of the spins of the register to comply with qiskit's
            little endian convention.

        Raises:
            QiskitColdAtomError:
                - If the length of the given probabilities does not math the expected Hilbert
                    space dimension.
                - If the dimension is not a power of the spin length of the solver.
                - If the number of shots self.shots has not been specified.
        """

        meas_dim = len(measurement_distribution)

        if meas_dim != self.dim:
            raise QiskitColdAtomError(
                f"Dimension of the measurement probabilities {meas_dim} does not "
                f"match the dimension expected by the solver, {self.dim}"
            )

        if self.shots is None:
            raise QiskitColdAtomError(
                "The number of shots has to be set before drawing measurements"
            )

        # Draw measurements as the indices of the basis states:
        meas_results = np.random.choice(range(meas_dim), self.shots, p=measurement_distribution)
        base = int(2 * self.spin + 1)
        num_wires = math.log(meas_dim, base)

        if num_wires.is_integer():
            num_wires = int(num_wires)
        else:
            raise QiskitColdAtomError(
                "The length of given measurement distribution it not compatible with "
                "the spin-length of the solver."
            )

        outcome_memory = []
        for meas_idx in meas_results:
            digits = [0] * num_wires
            for i in range(num_wires):
                digits[i] = meas_idx % base
                meas_idx //= base

            outcome_memory.append(" ".join(map(str, digits)))

        return outcome_memory
