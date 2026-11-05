// SPDX-License-Identifier: GPL-2.0-or-later
public extension Account {
    var localClientCosmetics: LocalClientCosmeticsContext {
        return LocalClientCosmeticsContext(postbox: self.postbox)
    }
}
