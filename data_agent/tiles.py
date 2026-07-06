"""
Re-export tile rendering functions from a2a.tile_handler.

This module re-exports the tile-rendering functions from tile_handler
as a convenience alias.
"""

from data_agent.tile_handler import generate_tile, tile_bounds, _make_png

__all__ = ['generate_tile', 'tile_bounds']
