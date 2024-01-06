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

"""Module to simulate fermionic circuits."""

from typing import List, Tuple, Optional
import numpy as np
from scipy.sparse import csc_matrix

from qiskit import QuantumCircuit
from qiskit_nature.operators.second_quantization import FermionicOp

from qiskit_cold_atom.base_circuit_solver import BaseCircuitSolver
from qiskit_cold_atom.exceptions import QiskitColdAtomError
from qiskit_cold_atom.fermions.fermionic_state import FermionicState
from qiskit_cold_atom.fermions.fermionic_basis import FermionicBasis
from qiskit_cold_atom.fermions.fermion_gate_library import FermionicGate


class FermionCircuitSolver(BaseCircuitSolver):
    """
    Numerically simulate fermionic systems by exactly computing the time
    evolution under unitary operations generated by fermionic Hamiltonians.
    """

    def __init__(
        self,
        shots: Optional[int] = None,
        seed: Optional[int] = None,
        num_species: int = 1,
    ):
        """
        Args:
            shots: amount of shots for the measurement simulation;
                if not None, measurements are performed
            seed: seed for the RNG for the measurement simulation
            num_species: number of different fermionic species, defaults to 1 for a single type of
                (spinless) fermions, 2 for spin-1/2 fermions etc. If > 1, the solver will check for
                conservation of the particle number per fermionic species in order to reduce the
                Hilbert space dimension of the simulation
        """
        self._basis = None
        self.num_species = num_species

        super().__init__(shots=shots, seed=seed)

    @property
    def basis(self) -> FermionicBasis:
        """
        Return the basis of fermionic occupation number states. This basis is updated via the
        setter whenever a new circuit is passed to __call__.
        """
        return self._basis

    @basis.setter
    def basis(self, basis: FermionicBasis):
        """
        Set the basis of the simulation and check its dimensions.

        Args:
            basis: The new basis.

        Raises:
            QiskitColdAtomError: If the dimension of the basis is too large.
        """
        if basis.dimension > self.max_dimension:
            raise QiskitColdAtomError(
                f"Dimension {basis.dimension} exceeds the maximum "
                f"allowed dimension {self.max_dimension}."
            )

        self._basis = basis

    def preprocess_circuit(self, circuit: QuantumCircuit):
        """
        Pre-processing fermionic circuits includes setting up the basis for the simulation
        by extracting the size, particle number and spin conservation from the circuit.

        Args:
            circuit: A fermionic quantum circuit for which to setup a basis.
        """
        initial_occupations = FermionicState.initial_state(circuit, self.num_species)
        _, spin_conservation = self._check_conservations(circuit)
        self.basis = FermionicBasis.from_state(initial_occupations, spin_conservation)
        self._dim = self.basis.dimension

    def get_initial_state(self, circuit: QuantumCircuit) -> csc_matrix:
        """
        Return the initial state of the quantum circuit as a sparse column vector.

        Args:
            circuit: The circuit for which to extract the initial_state.

        Returns:
            The initial state of the circuit as a sparse matrix.
        """

        init_state = FermionicState.initial_state(circuit, self.num_species)
        initial_occs = init_state.occupations_flat
        initial_index = self.basis.get_occupations().index(initial_occs)

        initial_state = csc_matrix(
            ([1 + 0j], ([initial_index], [0])),
            shape=(self.basis.dimension, 1),
            dtype=complex,
        )

        return initial_state

    def _embed_operator(
        self, operator: FermionicOp, num_wires: int, qargs: List[int]
    ) -> FermionicOp:
        """
        Turn a FermionicOp operator that acts on the wires given in qargs into an operator
        that acts on the entire state space of the circuit by padding with identities "I" on the
        remaining wires

        Args:
            operator: FermionicOp describing the generating Hamiltonian of a gate
            num_wires: The total number of wires in which the operator should be embedded into
            qargs: The wire indices the gate acts on

        Returns:
            FermionicOp, an operator acting on the entire quantum register of the Circuit

        Raises:
            QiskitColdAtomError:
                - If the given operator is not a FermionicOp
                - If the size of the operator does not match the given qargs
        """

        if not isinstance(operator, FermionicOp):
            raise QiskitColdAtomError(
                f"Expected FermionicOp; got {type(operator).__name__} instead."
            )

        if operator.register_length != len(qargs):
            raise QiskitColdAtomError(
                f"length of gate labels {operator.register_length} does not match "
                f"qargs {qargs} of the gates"
            )

        embedded_terms = []

        for term, coeff in operator.terms():
            embedded_term = []

            for action, index in term:
                embedded_term.append((action, qargs[index]))

            embedded_terms.append(((embedded_term, coeff)))

        return FermionicOp(embedded_terms, register_length=num_wires, display_format="dense")

    def _check_conservations(self, circuit: QuantumCircuit) -> Tuple[bool, bool]:
        """
        Check if the fermionic operators defined in the circuit conserve the total particle number
        (i.e. there are as many creation operators as annihilation operators) and the particle
        number per spin species (e.g. there are as many up/down creation operators as there are
        up/down annihilation operators).

        Args:
            circuit: A quantum circuit with fermionic gates

        Returns:
            particle_conservation: True if the particle number is conserved in the circuit
            spin_conservation: True if the particle number is conserved for each spin species

        Raises:
            QiskitColdAtomError:
                - If an operator in the circuit is not a FermionicOp.
                - If the length of the fermionic operators does not match the system size.
                - If the circuit has a number of wires that is not a multiple of the number
                  of fermionic species.
        """
        particle_conservation = True
        spin_conservation = True

        for fermionic_op in self.to_operators(circuit):
            if not isinstance(fermionic_op, FermionicOp):
                raise QiskitColdAtomError("operators need to be given as FermionicOp")

            for term in fermionic_op.to_list():
                opstring = term[0]

                if len(opstring) != circuit.num_qubits:
                    raise QiskitColdAtomError(
                        f"Expected length {circuit.num_qubits} for fermionic operator; "
                        f"received {len(opstring)}."
                    )

                num_creators = opstring.count("+")
                num_annihilators = opstring.count("-")

                if num_creators != num_annihilators:
                    return False, False

                if self.num_species > 1:
                    if circuit.num_qubits % self.num_species != 0:
                        raise QiskitColdAtomError(
                            f"The number of wires in the circuit {circuit.num_qubits} is not a "
                            f"multiple of the {self.num_species} fermionic species number."
                        )

                    sites = circuit.num_qubits // self.num_species

                    # check if the particle number is conserved for each spin species
                    for i in range(self.num_species):
                        ops = opstring[i * sites : (i + 1) * sites]
                        num_creators = ops.count("+")
                        num_annihilators = ops.count("-")

                        if num_creators != num_annihilators:
                            spin_conservation = False
                            break

        return particle_conservation, spin_conservation

    def operator_to_mat(self, operator: FermionicOp) -> csc_matrix:
        """Convert the fermionic operator to a sparse matrix.

        Args:
            operator: fermionic operator of which to compute the matrix representation

        Returns:
            scipy.sparse matrix of the Hamiltonian

        """
        return FermionicGate.operator_to_mat(operator, self.num_species, self._basis)

    def draw_shots(self, measurement_distribution: List[float]) -> List[str]:
        """
        Helper function to draw counts from a given distribution of measurement outcomes.

        Args:
            measurement_distribution: List of probabilities of the individual measurement outcomes

        Returns:
            a list of individual measurement results, e.g. ["011000", "100010", ...]
            The outcome of each shot is denoted by a binary string of the occupations of the individual
            modes in little endian convention

        Raises:
            QiskitColdAtomError:
                - If the length of the given probabilities does not match the expected Hilbert space
                dimension.
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

        # list all possible outcomes as strings '001011', reversing the order of the wires
        # to comply with Qiskit's ordering convention
        outcome_strings = ["".join(map(str, k)) for k in self.basis.get_occupations()]

        # Draw measurements:
        meas_results = np.random.choice(outcome_strings, self.shots, p=measurement_distribution)

        return meas_results.tolist()
