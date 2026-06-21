// The TRUE target for the query "cell size": the contract that establishes how big a
// single grid cell is. Its doc avoids the literal word "size" (it says "dimensions" /
// "extent"), so a code embedding does NOT rank it on the "size" token — and nothing else
// here carries "cell" either, so the top hits collapse onto the `.size()` family with no
// "cell" coverage at all (the measured T2 failure signature). The word "size" reaches the
// target only through its NAME (rescaleCellSize), which is what fan-out + coverage
// re-rank exploit to recover it.
#include <cstddef>

/// World-space grid of square cells that is rebuilt when the viewport changes.
struct Grid {
    int columns_;
    double worldWidth_;

    /// Recomputes each cell's edge dimensions so the grid exactly fills the resized world.
    double rescaleCellSize(double worldWidth, int columns) {
        worldWidth_ = worldWidth;
        columns_ = columns;
        return worldWidth_ / static_cast<double>(columns_);
    }
};
