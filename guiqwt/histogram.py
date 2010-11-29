# -*- coding: utf-8 -*-
#
# Copyright © 2009-2010 CEA
# Pierre Raybaut
# Licensed under the terms of the CECILL License
# (see guiqwt/__init__.py for details)

"""
guiqwt.histogram
----------------

The `histogram` module provides histogram related objects:
    * :py:class:`guiqwt.histogram.HistogramItem`: an histogram plot item
    * :py:class:`guiqwt.histogram.ContrastAdjustment`: the `contrast 
      adjustment panel`
    * :py:class:`guiqwt.histogram.LevelsHistogram`: a curve plotting widget 
      used by the `contrast adjustment panel` to compute, manipulate and 
      display the image levels histogram

``HistogramItem`` objects are plot items (derived from QwtPlotItem) that may 
be displayed on a 2D plotting widget like :py:class:`guiqwt.curve.CurvePlot` 
or :py:class:`guiqwt.image.ImagePlot`.

Example
~~~~~~~

Simple histogram plotting example:

.. literalinclude:: ../guiqwt/tests/histogram.py

Reference
~~~~~~~~~

.. autoclass:: HistogramItem
   :members:
   :inherited-members:
.. autoclass:: ContrastAdjustment
   :members:
   :inherited-members:
.. autoclass:: LevelsHistogram
   :members:
   :inherited-members:
"""
import numpy as np
from PyQt4.QtCore import Qt
from PyQt4.QtGui import QHBoxLayout, QVBoxLayout, QToolBar
from PyQt4.Qwt5 import QwtPlotCurve

from guidata.dataset.datatypes import DataSet
from guidata.dataset.dataitems import FloatItem
from guidata.utils import assert_interfaces_valid, update_dataset
from guidata.configtools import get_icon, get_image_layout
from guidata.qthelpers import add_actions, create_action

# Local imports
from guiqwt.config import CONF, _
from guiqwt.interfaces import (IBasePlotItem, IHistDataSource,
                               IVoiImageItemType, IPanel)
from guiqwt.panels import PanelWidget, ID_CONTRAST
from guiqwt.curve import CurveItem, CurvePlot
from guiqwt.image import ImagePlot
from guiqwt.styles import HistogramParam, CurveParam
from guiqwt.shapes import XRangeSelection
from guiqwt.tools import (SelectTool, BasePlotMenuTool, SelectPointTool,
                          AntiAliasingTool)
from guiqwt.signals import (SIG_RANGE_CHANGED, SIG_VOI_CHANGED,
                            SIG_ITEM_SELECTION_CHANGED, SIG_ACTIVE_ITEM_CHANGED)
from guiqwt.plot import PlotManager


class HistDataSource(object):
    """
    An objects that provides an Histogram data source interface
    to a simple numpy array of data
    """
    __implements__ = (IHistDataSource,)
    def __init__(self, data):
        self.data = data

    def get_histogram(self, nbins):
        """Returns the histogram computed for nbins bins"""
        return np.histogram(self.data, nbins)

assert_interfaces_valid(HistDataSource)


def lut_range_threshold(item, bins, percent):
    hist, bin_edges = item.get_histogram(bins)
    hist = np.concatenate((hist, [0]))
    threshold = .5*percent/100*hist.sum()
    i_bin_min = np.cumsum(hist).searchsorted(threshold)
    i_bin_max = -1-np.cumsum(np.flipud(hist)).searchsorted(threshold)
    return bin_edges[i_bin_min], bin_edges[i_bin_max]


class HistogramItem(CurveItem):
    """A Qwt item representing histogram data"""
    __implements__ = (IBasePlotItem,)
    
    def __init__(self, curveparam=None, histparam=None):
        self.hist_count = None
        self.hist_bins = None
        self.bins = None
        self.old_bins = None
        self.source = None
        self.logscale = None
        self.remove_first_bin = None
        self.old_logscale = None
        if histparam is None:
            self.histparam = HistogramParam(title=_("Histogram"),
                                            icon='histogram.png')
        else:
            self.histparam = histparam
        CurveItem.__init__(self, curveparam)
        self.setStyle(QwtPlotCurve.Steps)
        self.setCurveAttribute(QwtPlotCurve.Inverted)
            
    def set_hist_source(self, src):
        """
        Set histogram source
        (source: object with method 'get_histogram',
         e.g. objects derived from guiqwt.image.ImageItem)
        """
        self.source = src
        self.update_histogram()
        
    def set_hist_data(self, data):
        """Set histogram data"""
        self.set_hist_source(HistDataSource(data))        

    def set_logscale(self, state):
        """Sets whether we use a logarithm or linear scale
        for the histogram counts"""
        self.logscale = state
        self.update_histogram()
        
    def get_logscale(self):
        """Returns the status of the scale"""
        return self.logscale
        
    def set_remove_first_bin(self, state):
        self.remove_first_bin = state
        self.update_histogram()
        
    def get_remove_first_bin(self):
        return self.remove_first_bin

    def set_bins(self, n_bins):
        self.bins = n_bins
        self.update_histogram()
        
    def get_bins(self):
        return self.bins
        
    def compute_histogram(self):
        return self.source.get_histogram(self.bins)
        
    def update_histogram(self):
        if self.source is None:
            return
        hist, bin_edges = self.compute_histogram()
        hist = np.concatenate((hist, [0]))
        if self.remove_first_bin:
            hist[0] = 0
        if self.logscale:
            hist = np.log(hist+1)

        self.set_data(bin_edges, hist)
        # Autoscale only if logscale/bins have changed
        if self.bins != self.old_bins or self.logscale != self.old_logscale:
            if self.plot():
                self.plot().do_autoscale()
        self.old_bins = self.bins
        self.old_logscale = self.logscale
        
        plot = self.plot()
        if plot is not None:
            plot.do_autoscale(replot=True)

    def update_params(self):
        self.histparam.update_hist(self)
        super(HistogramItem, self).update_params()

    def get_item_parameters(self, itemparams):
        super(HistogramItem, self).get_item_parameters(itemparams)
        itemparams.add("HistogramParam", self, self.histparam)
    
    def set_item_parameters(self, itemparams):
        update_dataset(self.histparam, itemparams.get("HistogramParam"),
                       visible_only=True)
        self.histparam.update_hist(self)
        super(HistogramItem, self).set_item_parameters(itemparams)

assert_interfaces_valid(HistogramItem)


class LevelsHistogram(CurvePlot):
    """Image levels histogram widget"""
    def __init__(self, parent=None):
        super(LevelsHistogram, self).__init__(parent=parent, title="",
                                              section="histogram")
        self.antialiased = False

        # a dict of dict : plot -> selected items -> HistogramItem
        self._tracked_items = {}
        self.curveparam = CurveParam(_("Curve"), icon="curve.png")
        self.curveparam.read_config(CONF, "histogram", "curve")
        
        self.histparam = HistogramParam(_("Histogram"), icon="histogram.png")
        self.histparam.logscale = False
        self.histparam.remove_first_bin = True
        self.histparam.n_bins = 256

        self.range = XRangeSelection(0, 1)
        self.range_mono_color = self.range.shapeparam.sel_line.color
        self.range_multi_color = CONF.get("histogram",
                                          "range/multi/color", "red")
        
        self.add_item(self.range, z=5)
        self.connect(self, SIG_RANGE_CHANGED, self.range_changed)
        self.set_active_item(self.range)

        self.setMinimumHeight(80)
        self.setAxisMaxMajor(self.yLeft, 5)
        self.setAxisMaxMinor(self.yLeft, 0)

        if parent is None:
            self.set_axis_title('bottom', 'Levels')

    def connect_plot(self, plot):
        if not isinstance(plot, ImagePlot):
            # Connecting only to image plot widgets (allow mixing image and 
            # curve widgets for the same plot manager -- e.g. in pyplot)
            return
        self.connect(self, SIG_VOI_CHANGED, plot.notify_colormap_changed)
        self.connect(plot, SIG_ITEM_SELECTION_CHANGED, self.selection_changed)
        self.connect(plot, SIG_ACTIVE_ITEM_CHANGED, self.active_item_changed)
        
    def standard_tools(self, manager):
        manager.add_tool(SelectTool)
        manager.add_tool(BasePlotMenuTool, "item")
        manager.add_tool(BasePlotMenuTool, "axes")
        manager.add_tool(BasePlotMenuTool, "grid")
        manager.add_tool(AntiAliasingTool)
        manager.get_default_tool().activate()

    def tracked_items_gen(self):
        for plot, items in self._tracked_items.items():
            for item in items.items():
                yield item # tuple item,curve

    def __del_known_items(self, known_items, items):
        del_curves = []
        for item in known_items.keys():
            if item not in items:
                curve = known_items.pop(item)
                del_curves.append(curve)
        self.del_items(del_curves)

    def selection_changed(self, plot):
        items = plot.get_selected_items(IVoiImageItemType)
        known_items = self._tracked_items.setdefault(plot, {})

        if items:
            self.__del_known_items(known_items, items)
            if len(items) == 1:
                # Removing any cached item for other plots
                for other_plot, _items in self._tracked_items.items():
                    if other_plot is not plot:
                        if not other_plot.get_selected_items(IVoiImageItemType):
                            other_known_items = self._tracked_items[other_plot]
                            self.__del_known_items(other_known_items, [])
        else:
            # if all items are deselected we keep the last known
            # selection (for one plot only)
            for other_plot, _items in self._tracked_items.items():
                if other_plot.get_selected_items(IVoiImageItemType):
                    self.__del_known_items(known_items, [])
                    break
                
        for item in items:
            if item not in known_items:
                curve = HistogramItem(self.curveparam, self.histparam)
                curve.set_hist_source(item)
                self.add_item(curve, z=0)
                known_items[item] = curve

        nb_selected = len(list(self.tracked_items_gen()))
        if not nb_selected:
            self.replot()
            return
        self.curveparam.shade = 1.0/nb_selected
        for item, curve in self.tracked_items_gen():
            self.curveparam.update_curve(curve)
            self.histparam.update_hist(curve)

        self.active_item_changed(plot)

    def active_item_changed(self, plot):
        items = plot.get_selected_items(IVoiImageItemType)
        if not items:
            #XXX: workaround
            return
            
        active = plot.get_last_active_item(IVoiImageItemType)
        if active:
            active_range = active.get_lut_range()
        else:
            active_range = None
        
        multiple_ranges = False
        for item, curve in self.tracked_items_gen():
            if active_range != item.get_lut_range():
                multiple_ranges = True
        if active_range is not None:
            _m, _M = active_range
            self.set_range_style(multiple_ranges)
            self.range.set_range(_m, _M, dosignal=False)
        self.replot()
    
    def set_range_style(self, multiple_ranges):
        if multiple_ranges:
            self.range.shapeparam.sel_line.color = self.range_multi_color
        else:
            self.range.shapeparam.sel_line.color = self.range_mono_color
        self.range.shapeparam.update_range(self.range)

    def set_range(self, _min, _max):
        if _min < _max:
            self.set_range_style(False)
            self.range.set_range(_min, _max)
            self.replot()
            return True
        else:
            # Range was not changed
            return False

    def range_changed(self, _rangesel, _min, _max):
        for item, curve in self.tracked_items_gen():
            item.set_lut_range([_min, _max])
        self.emit(SIG_VOI_CHANGED)
        
    def set_full_range(self):
        """Set range bounds to image min/max levels"""
        _min = _max = None
        for item, curve in self.tracked_items_gen():
            imin, imax = item.get_lut_range_full()
            if _min is None or _min>imin:
                _min = imin
            if _max is None or _max<imax:
                _max = imax
        if _min is not None:
            self.set_range(_min, _max)

    def apply_min_func(self, item, curve, min):
        _min, _max = item.get_lut_range()
        return min, _max

    def apply_max_func(self, item, curve, max):
        _min, _max = item.get_lut_range()
        return _min, max

    def reduce_range_func(self, item, curve, percent):
        return lut_range_threshold(item, curve.bins, percent)
        
    def apply_range_function(self, func, *args, **kwargs):
        item = None
        for item, curve in self.tracked_items_gen():
            _min, _max = func(item, curve, *args, **kwargs)
            item.set_lut_range([_min, _max])
        self.emit(SIG_VOI_CHANGED)
        if item is not None:
            self.active_item_changed(item.plot())
        
    def eliminate_outliers(self, percent):
        """
        Eliminate outliers:
        eliminate percent/2*N counts on each side of the histogram
        (where N is the total count number)
        """
        self.apply_range_function(self.reduce_range_func, percent)
        
    def set_min(self, _min):
        self.apply_range_function(self.apply_min_func, _min)
    
    def set_max(self, _max):
        self.apply_range_function(self.apply_max_func, _max)
        

class EliminateOutliersParam(DataSet):
    percent = FloatItem(_("Eliminate outliers")+" (%)",
                        default=2., min=0., max=100.-1e-6)


class ContrastAdjustment(PanelWidget):
    """Contrast adjustment tool"""
    __implements__ = (IPanel,)
    PANEL_ID = ID_CONTRAST

    def __init__(self, parent=None):
        super(ContrastAdjustment, self).__init__(parent)
        widget_title = _("Contrast adjustment tool")
        widget_icon = "contrast.png"
        
        self.local_manager = None # local manager for the histogram plot
        self.manager = None # manager for the associated image plot
        
        # Storing min/max markers for each active image
        self.min_markers = {}
        self.max_markers = {}
        
        # Select point tools
        self.min_select_tool = None
        self.max_select_tool = None
        
        style = "<span style=\'color: #444444\'><b>%s</b></span>"
        layout, _label = get_image_layout(widget_icon,
                                          style % widget_title,
                                          alignment=Qt.AlignCenter)
        layout.setAlignment(Qt.AlignCenter)
        vlayout = QVBoxLayout()
        vlayout.addLayout(layout)
        self.local_manager = PlotManager(self)
        self.histogram = LevelsHistogram(parent)
        vlayout.addWidget(self.histogram)
        self.local_manager.add_plot(self.histogram)
        hlayout = QHBoxLayout()
        self.setLayout(hlayout)
        hlayout.addLayout(vlayout)
        
        self.toolbar = toolbar = QToolBar(self)
        toolbar.setOrientation(Qt.Vertical)
#        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        hlayout.addWidget(toolbar)
        self.histogram.standard_tools(self.local_manager)
        self.setWindowIcon(get_icon(widget_icon))
        self.setWindowTitle(widget_title)
        
        self.outliers_param = EliminateOutliersParam(widget_title)
        
    def register_panel(self, manager):
        """Register panel to plot manager"""
        self.manager = manager
        default_toolbar = self.manager.get_default_toolbar()
        self.manager.add_toolbar(self.toolbar, "contrast")
        self.manager.set_default_toolbar(default_toolbar)
        self.setup_actions()
        for plot in manager.get_plots():
            self.histogram.connect_plot(plot)

    def get_plot(self):
        return self.manager.get_active_plot()

    def closeEvent(self, event):
        self.hide()
        event.ignore()
        
    def setup_actions(self):
        fullrange_ac = create_action(self, _("Full range"),
                                     icon=get_icon("full_range.png"),
                                     triggered=self.histogram.set_full_range,
                                     tip=_("Scale the image's display range "
                                           "according to data range") )
        autorange_ac = create_action(self, _("Eliminate outliers"),
                                     icon=get_icon("eliminate_outliers.png"),
                                     triggered=self.eliminate_outliers,
                                     tip=_("Eliminate levels histogram "
                                           "outliers and scale the image's "
                                           "display range accordingly") )
        add_actions(self.toolbar,[fullrange_ac, autorange_ac])
        self.min_select_tool = self.manager.add_tool(SelectPointTool,
                                       title=_("Minimum level"),
                                       on_active_item=True,mode="create",
                                       tip=_("Select minimum level on image"),
                                       toolbar_id="contrast",
                                       end_callback=self.apply_min_selection)
        self.max_select_tool = self.manager.add_tool(SelectPointTool,
                                       title=_("Maximum level"),
                                       on_active_item=True,mode="create",
                                       tip=_("Select maximum level on image"),
                                       toolbar_id="contrast",
                                       end_callback=self.apply_max_selection)        
    
    def eliminate_outliers(self):
        def apply(param):
            self.histogram.eliminate_outliers(param.percent)
        if self.outliers_param.edit(self, apply=apply):
            apply(self.outliers_param)

    def apply_min_selection(self, tool):
        item = self.get_plot().get_last_active_item(IVoiImageItemType)
        point = self.min_select_tool.get_coordinates()
        z = item.get_data(*point)
        self.histogram.set_min(z)

    def apply_max_selection(self, tool):
        item = self.get_plot().get_last_active_item(IVoiImageItemType)
        point = self.max_select_tool.get_coordinates()
        z = item.get_data(*point)
        self.histogram.set_max(z)
        
    def set_range(self, _min, _max):
        """Set contrast panel's histogram range"""
        self.histogram.set_range(_min, _max)

assert_interfaces_valid(ContrastAdjustment)
