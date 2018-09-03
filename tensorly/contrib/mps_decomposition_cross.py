import tensorly as tl
from ..mps_tensor import mps_to_tensor
from ..random import check_random_state
import numpy as np

rng = check_random_state(1)


def matrix_product_state_cross(input_tensor, rank, tol=1e-5, n_iter_max=100):
    """MPS (tensor-train) decomposition via cross-approximation (TTcross) [1]

        Decomposes `input_tensor` into a sequence of order-3 tensors of given rank. (factors/cores)
        Rather than directly decompose the whole tensor, we sample fibers based on skeleton decomposition.
        We initialize a random tensor-train and sweep from left to right and right to left.
        On each core, we shape the core as a matrix and choose the fibers indices by finding maximum-volume submatrix and update the core.

        Advantage: faster
            The main advantage of TTcross is that it doesn't need to evaluate all the entries of the tensor.
            For a n^d tensor, SVD needs O(n^d) runtime, but TTcross' runtime is linear in n and d, which makes it feasible in high dimension.
        Disadvantage: less accurate
            TTcross may underestimate the error, since it only evaluates partial entries of the tensor.
            Besides, in contrast to its practical fast performance, there is no theoretical guarantee of it convergence.

    Parameters
    ----------
    input_tensor : tensorly.tensor
            The tensor to decompose.
    rank : {int, int list}
            maximum allowable MPS rank of the factors
            if int, then this is the same for all the factors
            if int list, then rank[k] is the rank of the kth factor
    tol : float
            accuracy threshold for outer while-loop
    n_iter_max : int
            maximum iterations of outer while-loop (the 'crosses' or 'sweeps' sampled)

    Returns
    -------
    factors : MPS factors
              order-3 tensors of the MPS decomposition

    Examples
    --------

    Generate a 5^3 tensor, and decompose it into tensor-train of 3 factors, with rank = [1,3,3,1]
    >>> tensor = tl.tensor(np.arange(5**3).reshape(5,5,5))
    >>> rank = [1, 3, 3, 1]
    >>> factors = matrix_product_state_cross(tensor, rank)
    print the first core:
    >>> print(factors[0])
    .[[[ 24.   0.   4.]
      [ 49.  25.  29.]
      [ 74.  50.  54.]
      [ 99.  75.  79.]
      [124. 100. 104.]]]

    Notes
    -----
    Pseudo-code [2]:
    1. Initialization d cores and column indices
    2. while (error > tol)
    3.    update the tensor-train from left to right:
                for Core 1 to Core d
                    approximate the skeleton-decomposition by QR and maxvol
    4.    update the tensor-train from right to left:
                for Core d to Core 1
                    approximate the skeleton-decomposition by QR and maxvol
    5. end while

    Acknowledgement: the main body of the code is modified based on TensorToolbox by Daniele Bigoni

    References
    ----------
    .. [1] Ivan Oseledets and Eugene Tyrtyshnikov.  Tt-cross approximation for multidimensional arrays.
            LinearAlgebra and its Applications, 432(1):70–88, 2010.
    .. [2] Sergey Dolgov and Robert Scheichl. A hybrid alternating least squares–tt cross algorithm for parametricpdes.
            arXiv preprint arXiv:1707.04562, 2017.
    """

    # Check user input for errors
    n = tl.shape(input_tensor)
    d = tl.ndim(input_tensor)

    if isinstance(rank, int):
        rank = [rank] * (d + 1)
    elif d + 1 != len(rank):
        message = 'Provided incorrect number of ranks. Should verify len(rank) == tl.ndim(tensor)+1, but len(rank) = {} while tl.ndim(tensor) + 1  = {}'.format(
            len(rank), d)
        raise (ValueError(message))

    # Make sure iter's not a tuple but a list
    rank = list(rank)

    # Initialize rank
    if rank[0] != 1:
        print(
            'Provided rank[0] == {} but boundaring conditions dictatate rank[0] == rank[-1] == 1: setting rank[0] to 1.'.format(
                rank[0]))
        rank[0] = 1
    if rank[-1] != 1:
        print(
            'Provided rank[-1] == {} but boundaring conditions dictatate rank[0] == rank[-1] == 1: setting rank[-1] to 1.'.format(
                rank[0]))

    # list col_idx: column indices (right indices) for skeleton-decomposition: indicate which columns used in each core.
    # list row_idx: row indices    (left indices)  for skeleton-decomposition: indicate which rows used in each core.

    # Initialize indice: random selection of column indices
    col_idx = [None] * d
    for k_col_idx in range(d - 1):
        col_idx[k_col_idx] = []
        for i in range(rank[k_col_idx + 1]):
            newidx = tuple([rng.randint(n[j]) for j in range(k_col_idx + 1, d)])
            while newidx in col_idx[k_col_idx]:
                newidx = tuple([rng.randint(n[j]) for j in range(k_col_idx + 1, d)])

            col_idx[k_col_idx].append(newidx)

    # Initialize the cores of tensor-train
    factor_old = [tl.zeros((rank[k], n[k], rank[k + 1])) for k in range(d)]
    factor_new = [tl.tensor(rng.random_sample((rank[k], n[k], rank[k + 1]))) for k in range(d)]

    iter = 0

    error = tl.norm(mps_to_tensor(factor_old) - mps_to_tensor(factor_new), 2)
    threshold = tol * tl.norm(mps_to_tensor(factor_new), 2)
    for iter in range(n_iter_max):
        if error < threshold:
            break

        factor_old = factor_new
        factor_new = [None for i in range(d)]

        ######################################
        # left-to-right step
        left_to_right_fiberlist = []
        # list row_idx: list of (d-1) of lists of left indices
        row_idx = [[()]]
        for k in range(d - 1):
            (next_row_idx, fibers_list) = left_right_ttcross_step(input_tensor, k, rank, row_idx, col_idx)
            # update row indices
            left_to_right_fiberlist.extend(fibers_list)
            row_idx.append(next_row_idx)

        # end left-to-right step
        ###############################################

        ###############################################
        # right-to-left step
        right_to_left_fiberlist = []
        # list col_idx: list (d-1) of lists of right indices
        col_idx = [None] * d
        col_idx[-1] = [()]
        for k in range(d, 1, -1):
            (next_col_idx, fibers_list, Q_skeleton) = right_left_ttcross_step(input_tensor, k, rank, row_idx, col_idx)
            # update col indices
            right_to_left_fiberlist.extend(fibers_list)
            col_idx[k - 2] = next_col_idx

            # Compute cores
            try:
                factor_new[k - 1] = tl.transpose(Q_skeleton)
                factor_new[k - 1] = tl.reshape(factor_new[k - 1], (rank[k - 1], n[k - 1], rank[k]))
            except:
                # The rank should not be larger than the input tensor's size
                raise (ValueError("The rank is too large compared to the size of the tensor. Try with small rank."))

        # Add the last core
        idx = (slice(None, None, None),) + tuple(zip(*col_idx[0]))

        core = input_tensor[idx]
        core = tl.reshape(core, (n[0], 1, rank[1]))
        core = tl.transpose(core, (1, 0, 2))

        factor_new[0] = core

        # end right-to-left step
        ################################################

        # check the error for while-loop
        error = tl.norm(mps_to_tensor(factor_old) - mps_to_tensor(factor_new), 2)
        threshold = tol * tl.norm(mps_to_tensor(factor_new), 2)

    # check convergence
    if iter >= n_iter_max:
        raise ValueError('Maximum number of iterations reached.')
    if tl.norm(mps_to_tensor(factor_old) - mps_to_tensor(factor_new), 2) > tol * tl.norm(mps_to_tensor(factor_new), 2):
        raise ValueError('Low Rank Approximation algorithm did not converge.')

    return factor_new


def left_right_ttcross_step(input_tensor, k, rank, row_idx, col_idx):
    """ Compute the next (right) core's row indices by QR decomposition.

            For the current Tensor train core, we use the row indices and col indices to extract the entries from the input tensor
            and compute the next core's row indices by QR and max volume algorithm.

    Parameters
    ----------

    k: int
            the actual sweep iteration
    rank: list of int
            list of upper ranks (d)
    row_idx: list of list of int
            list of (d-1) of lists of left indices
    col_idx: list of list of int
            list of (d-1) of lists of right indices

    Returns
    -------
    next_row_idx : list of int
            the list of new row indices,
    fibers_list : list of slice
            the used fibers,
    Q_skeleton : matrix
            approximation of Q as product of Q and inverse of its maximum volume submatrix
    """

    n = tl.shape(input_tensor)
    d = tl.ndim(input_tensor)
    fibers_list = []

    # Extract fibers according to the row and col indices
    for i in range(rank[k]):
        for j in range(rank[k + 1]):
            fiber = row_idx[k][i] + (slice(None, None, None),) + col_idx[k][j]
            fibers_list.append(fiber)
    if k == 0:  # Is[k] will be empty
        idx = (slice(None, None, None),) + tuple(zip(*col_idx[k]))
    else:
        idx = [[] for i in range(d)]
        for lidx in row_idx[k]:
            for ridx in col_idx[k]:
                for j, jj in enumerate(lidx): idx[j].append(jj)
                for j, jj in enumerate(ridx): idx[len(lidx) + 1 + j].append(jj)
        idx[k] = slice(None, None, None)
        idx = tuple(idx)

    # Extract the core
    core = input_tensor[idx]
    # shape the core as a 3-d cube
    if k == 0:
        core = tl.reshape(core, (n[k], rank[k], rank[k + 1]))
        core = tl.transpose(core, (1, 0, 2))
    else:
        core = tl.reshape(core, (rank[k], rank[k + 1], n[k]))
        core = tl.transpose(core, (0, 2, 1))

    # merge r_k and n_k, get a matrix
    core = tl.reshape(core, (rank[k] * n[k], rank[k + 1]))

    # Compute QR decomposition
    (Q, R) = tl.qr(core)

    # Maxvol
    (I, _) = maxvol(Q)

    # Retrive indices in folded tensor
    new_idx = [np.unravel_index(idx, [rank[k], n[k]]) for idx in I]  # First retrive idx in folded core
    next_row_idx = [row_idx[k][ic[0]] + (ic[1],) for ic in new_idx]  # Then reconstruct the idx in the tensor

    return (next_row_idx, fibers_list)


def right_left_ttcross_step(input_tensor, k, rank, row_idx, col_idx):
    """ Compute the next (left) core's col indices by QR decomposition.

            For the current Tensor train core, we use the row indices and col indices to extract the entries from the input tensor
            and compute the next core's col indices by QR and max volume algorithm.

    Parameters
    ----------

    k: int
            the actual sweep iteration
    rank: list of int
            list of upper rank (d)
    row_idx: list of list of int
            list of (d-1) of lists of left indices
    col_idx: list of list of int
            list of (d-1) of lists of right indices

    Returns
    -------
    next_col_idx : list of int
            the list of new col indices,
    fibers_list : list of slice
            the used fibers,
    Q_skeleton : matrix
            approximation of Q as product of Q and inverse of its maximum volume submatrix
    """

    n = tl.shape(input_tensor)
    d = tl.ndim(input_tensor)
    fibers_list = []

    # Extract fibers
    for i in range(rank[k - 1]):
        for j in range(rank[k]):
            fiber = row_idx[k - 1][i] + (slice(None, None, None),) + col_idx[k - 1][j]
            fibers_list.append(fiber)

    if k == d:  # Is[k] will be empty
        idx = tuple(zip(*row_idx[k - 1])) + (slice(None, None, None),)
    else:
        idx = [[] for i in range(d)]
        for lidx in row_idx[k - 1]:
            for ridx in col_idx[k - 1]:
                for j, jj in enumerate(lidx): idx[j].append(jj)
                for j, jj in enumerate(ridx): idx[len(lidx) + 1 + j].append(jj)
        idx[k - 1] = slice(None, None, None)
        idx = tuple(idx)

    core = input_tensor[idx]
    # shape the core as a 3-d cube
    core = tl.reshape(core, (rank[k - 1], rank[k], n[k - 1]))
    core = tl.transpose(core, (0, 2, 1))
    # merge n_{k-1} and r_k, get a matrix
    core = tl.reshape(core, (rank[k - 1], n[k - 1] * rank[k]))
    core = tl.transpose(core)

    # Compute QR decomposition
    (Q, R) = tl.qr(core)
    # Maxvol
    (J, Q_inv) = maxvol(Q)
    Q_inv = tl.tensor(Q_inv)
    Q_skeleton = tl.dot(Q, Q_inv)

    # Retrive indices in folded tensor
    new_idx = [np.unravel_index(idx, [n[k - 1], rank[k]]) for idx in J]  # First retrive idx in folded core
    next_col_idx = [(jc[0],) + col_idx[k - 1][jc[1]] for jc in new_idx]  # Then reconstruct the idx in the tensor

    return (next_col_idx, fibers_list, Q_skeleton)


def maxvol(A):
    """ Find the rxr submatrix of maximal volume in A(nxr), n>=r

            We want to decompose matrix A as
                    A = A[:,J] * (A[I,J])^-1 * A[I,:]
            This algorithm helps us find this submatrix A[I,J] from A, which has the largest determinant.
            We greedily find vector of max norm, and subtract its projection from the rest of rows.

    Parameters
    ----------

    A: matrix
            The matrix to find maximal volume

    Returns
    -------
    row_idx: list of int
            is the list or rows of A forming the matrix with maximal volume,
    A_inv: matrix
            is the inverse of the matrix with maximal volume.

    References
    ----------
    S. A. Goreinov, I. V. Oseledets, D. V. Savostyanov, E. E. Tyrtyshnikov, N. L. Zamarashkin.
    How to find a good submatrix.Goreinov, S. A., et al.
    Matrix Methods: Theory, Algorithms and Applications: Dedicated to the Memory of Gene Golub. 2010. 247-256.

    Ali Çivril, Malik Magdon-Ismail
    On selecting a maximum volume sub-matrix of a matrix and related problems
    Theoretical Computer Science. Volume 410, Issues 47–49, 6 November 2009, Pages 4801-4811
    """

    (n, r) = tl.shape(A)

    # The index of row of the submatrix
    row_idx = tl.zeros(r)
    i = 0
    A_new = A
    # Rest of rows / unselected rows
    rest_of_rows = tl.tensor(list(range(n)))
    rest_of_rows = tl.int(rest_of_rows)

    # Find r rows iteratively
    while i < r:
        # Compute the square of norm of each row
        rows_norms = tl.sum(A_new ** 2, axis=1)

        # If a row is 0, we delete it.
        if any(rows_norms == 0):
            rest_of_rows = rest_of_rows[rows_norms != 0]
            A_new = A_new[rows_norms != 0]
            continue

        # Find the row of max norm
        max_row_idx = tl.argmax(rows_norms, axis=0)
        max_row = A[rest_of_rows[max_row_idx], :]

        # Compute the projection of max_row to other rows
        max_row = tl.transpose(max_row)
        projection = tl.dot(A_new, max_row) / tl.sqrt(rows_norms * rows_norms[max_row_idx])

        # Subtract the projection from A_new
        A_new = A_new - A_new * projection.reshape(A_new.shape[0], 1)

        # Delete the selected row
        mask = tl.int(tl.ones(A_new.shape[0]))
        mask[max_row_idx] = 0

        A_new = A_new[mask==1,:]
        # update the row_idx and rest_of_rows
        row_idx[i] = rest_of_rows[max_row_idx]
        i = i + 1
        rest_of_rows = rest_of_rows[mask==1]

    row_idx = tl.int(row_idx)
    inverse = tl.inverse(A[row_idx,:])
    row_idx = tl.to_numpy(row_idx)

    return row_idx, inverse

