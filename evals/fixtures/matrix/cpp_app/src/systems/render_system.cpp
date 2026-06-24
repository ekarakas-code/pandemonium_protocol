#include "engine/system.hpp"
#include "engine/world.hpp"

namespace engine {

// Draws every entity each frame. A no-op stand-in for the real renderer; the third
// implementation of the ISystem::update extension hook.
struct RenderSystem : ISystem {
    void update(World& world) override {
        for (int i = 0; i < world.size(); ++i) {
            drawEntity(world.positions[i]);
        }
    }
    void drawEntity(const Vec2& at) { (void)at; }
};

}  // namespace engine
