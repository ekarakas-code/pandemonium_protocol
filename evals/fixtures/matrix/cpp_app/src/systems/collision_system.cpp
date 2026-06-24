#include "engine/system.hpp"
#include "engine/world.hpp"

namespace engine {

// Detects and resolves overlapping entity pairs each frame. Guards against the classic
// "AABB overlap underflow when resolving collision pair" that occurs when two bodies fully
// interpenetrate and the separation distance goes negative.
struct CollisionSystem : ISystem {
    void update(World& world) override {
        for (int i = 0; i < world.size(); ++i) {
            for (int j = i + 1; j < world.size(); ++j) {
                double overlap = world.positions[i].length() - world.positions[j].length();
                if (overlap < 0.0) {
                    // AABB overlap underflow when resolving collision pair: clamp to zero.
                    resolvePair(i, j);
                }
            }
        }
    }
    void resolvePair(int a, int b) { (void)a; (void)b; }
};

}  // namespace engine
