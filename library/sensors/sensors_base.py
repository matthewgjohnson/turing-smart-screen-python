# SPDX-License-Identifier: GPL-3.0-or-later
# Base class for custom data sources

from abc import ABC, abstractmethod
from typing import List


class CustomDataSource(ABC):
    """Base class for custom sensor data sources."""

    @abstractmethod
    def as_numeric(self) -> float:
        """Return numeric value for graphs and progress bars."""
        pass

    @abstractmethod
    def as_string(self) -> str:
        """Return formatted string for text display."""
        pass

    @abstractmethod
    def last_values(self) -> List[float]:
        """Return list of recent values for line graphs."""
        pass
