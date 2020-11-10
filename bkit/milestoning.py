import bkit.markov
import deeptime.base
import networkx as nx
import numpy as np
import scipy.spatial as spatial


class MarkovianMilestoningModel(bkit.markov.ContinuousTimeMarkovModel):
    """Milestoning process governed by a continuous-time Markov chain."""

    def __init__(self, rate_matrix, milestones):
        """Milestoning model with given rate matrix and set of milestones.

        Parameters
        ----------
        rate_matrix : (M, M) ndarray
            Transition rate matrix, row infinitesimal stochastic.

        milestones : array_like
            Ordered set of milestones indexed by the states of the 
            underlying Markov model.
           
        """
        super().__init__(rate_matrix)
        self.milestones = milestones

    @property
    def milestones(self):
        """The milestones in indexed order."""
        return self._milestones

    @milestones.setter
    def milestones(self, value):
        if len(value) != self.n_states:
            msg = 'number of milestones must match dimension of rate matrix'
            raise ValueError(msg)
        self._milestones = value

    @property
    def transition_kernel(self):
        """Transition probability kernel of the embedded Markov chain."""
        return self.embedded_markov_model.transition_matrix

    @property
    def mean_lifetimes(self):
        """The mean lifetime associated with each milestone.""" 
        return 1 / self.jump_rates

    @property
    def stationary_fluxes(self):
        """The stationary flux vector."""
        return self.embedded_markov_model.stationary_distribution


class MarkovianMilestoningEstimator(deeptime.base.Estimator):
    """Estimator for Markovian milestoning models."""

    def __init__(self, reversible=True, n_samples=None):
        """Estimator for Markovian milestoning models.

        Parameters
        ----------
        reversible : bool, default=True
            If True, restrict the ensemble of transition matrices to those
            satisfying detailed balance.

        n_samples : int, default=None
            Number of samples to draw from the posterior distribution. 
            If `None`, only compute maximum likelihood estimate.
            
        """
        self.reversible = reversible
        self.n_samples = n_samples

    def fetch_model(self):
        """Return the estimated models.

        Returns
        -------
        model : MarkovianMilestoningModel or BayesianPosterior

        """


    def fit(self, data):
        

    def fit_from_discrete_timeseries(self, timeseries):
        pass

    def fit_from_schedules(self, schedules):
        """Fit model from data in the form of milestone schedules.

        Parameters
        ----------
        schedules : list of lists of pairs
            Lists of (milestone, lifetime) pairs obtained by 
            trajectory decomposition.

        """
        
        for schedule in schedules:
            for (a, t), (b, _) in zip(schedule[:-1], schedule[1:]):
                if a not in self._ix or b not in self._ix:
                    continue
                self._count_matrix[self._ix[a], self._ix[b]] += 1
                self._total_times[self._ix[a]] += t
        self._schedules += schedules


class TrajectoryDecomposer:
    """Path decomposition by milestoning with Voronoi tessellations."""

    def __init__(self, anchors, cutoff=np.inf, boxsize=None):
        """Trajectory decomposer with given (partitioned) set of anchors.

        Parameters
        ----------
        anchors : ndarray (N, d) or list of ndarray (N_i, d)
            Generating points for Voronoi tessellation. 
            If a list of ndarrays is given, each subset of anchors
            indicates a union of Voronoi cells that should be treated 
            as a single cell.

        cutoff : positive float, optional
            Maximum distance to nearest anchor. The region of space 
            beyond the cutoff is treated as a cell labeled `None`.
 
       boxsize : array_like or scalar, optional (not yet implemented)
            Apply d-dimensional toroidal topology (periodic boundaries).

        """
        if boxsize:
            raise NotImplementedError('patience')

        if type(anchors) is np.ndarray:
            self._parent_cell = {k: k for k in range(len(anchors))}
        else:
            self._parent_cell = {k: i for i, arr in enumerate(anchors)
                                      for k in range(len(arr))}
            anchors = np.concatenate(anchors)
        n_anchors, n_dim = anchors.shape 

        G = nx.Graph()
        if n_dim > 1:
            tri = spatial.Delaunay(anchors)
            indptr, indices = tri.vertex_neighbor_vertices
            G.add_edges_from([(k, l) for k in range(n_anchors-1) 
                              for l in indices[indptr[k]:indptr[k+1]]])
        else:
            G.add_edges_from([(k, k+1) for k in range(n_anchors-1)])
        partition = lambda k, l: self._parent_cell[k] == self._parent_cell[l]
        self._graph = nx.quotient_graph(G, partition, relabel=True)

        self._kdtree = spatial.cKDTree(anchors)    
        
        self._cutoff = cutoff
        if np.isfinite(cutoff):
            self._parent_cell.append(None)
    
    @property
    def cell_anchor_mapping(self):
        """Mapping from cells to anchor points."""
        mapping = dict((i, []) for i in self._graph.nodes)
        for k, i in self._parent_cell.items():
            mapping[i] += self._kdtree.data[k]
        return mapping

    @property
    def milestones(self):
        """List of milestones."""
        return list(frozenset(a) for a in self._graph.edges)
 
    def remove_milestone(self, i, j):
        """Remove the milestone between cells i and j.

        Parameters
        ----------
        i, j : int
            Indices of cells to merge. Cell `j` is merged into cell `i`, 
            and `j` is removed from the cell index set.

        """
        if not self._graph.has_edge(i, j):
            raise ValueError('milestone does not exist; cannot remove it')
        self._graph = nx.contracted_nodes(self._graph, i, j, self_loops=False)
        for k in self._parent_cell:
            if self._parent_cell[k] == j:
                self._parent_cell[k] = i

    def decompose(self, trajs, dt=1, forward=False):
        """Map trajectories to milestone schedules.

        Parameters
        ----------
        trajs : ndarray (T, d) or list of ndarray (T_i, d)
            Trajectories to be decomposed.

        dt : int or float, optional
            Trajectory sampling interval, positive (>0)

        forward : bool, optional
            If true, track the next milestone hit (forward commitment),
            rather than the last milestone hit (backward commitment).
    
        Returns
        -------
        schedules : list of list of tuple
            Sequences of (milestone, lifetime) pairs obtained by 
            trajectory decomposition.

        """ 
        if type(trajs) is np.ndarray:
            trajs = [trajs]
        return [self._traj_to_milestone_schedule(traj, dt, forward)
                for traj in trajs]

    def _is_time_resolved(schedule):
        """Check whether all transitions are between adjacent cells."""
        for (a, t) in schedule:
            if None in a or self._graph.has_edge(*a):
                continue
            return False
        return True

    def _traj_to_milestone_schedule(self, traj, dt=1, forward=False):
        _, ktraj = self._kdtree.query(traj, distance_upper_bound=self._cutoff)
        dtraj = [self._parent_cell[k] for k in ktraj]
        return dtraj_to_milestone_schedule(dtraj, dt, forward)
 

def dtraj_to_milestone_schedule(dtraj, dt=1, forward=False):
    """'Milestone' a discrete trajectory.

    Parameters
    ----------
    dtraj : list of int or ndarray(T, dtype=int)
        A discrete trajectory.

    dt : int or float, optional
        Trajectory sampling interval, positive (>0)

    forward : bool, optional
        If true, track the next milestone hit (forward commitment),
        rather than the last milestone hit (backward commitment).

    Returns
    -------
    schedule : list of tuple
        Sequence of (milestone, lifetime) pairs. For backward milestoning,
        the first milestone is set to `{None, dtraj[0]}`. For forward
        milestoning, the last milestone is set to `{dtraj[-1], None}`.
        (In fact the milestones are `frozenset`s, which are hashable.)
    
    """
    if forward_milestoning:
        dtraj = reversed(dtraj)
    milestones = [frozenset({None, dtraj[0]})]
    lifetimes = [0]
    for i, j in zip(dtraj[:-1], dtraj[1:]):
        lifetimes[-1] += dt
        if j not in milestones[-1]:
            milestones.append(frozenset({i, j}))
            lifetimes.append(0)
    schedule = list(zip(milestones, lifetimes))
    if forward_milestoning:
        return reversed(schedule)
    return schedule

