import database
import app
import template
import views
import feed
import folder
import resource
import guide
import playlist

from xml.dom.minidom import parse
from gtcache import gettext as _

###############################################################################
#### Tabs                                                                  ####
###############################################################################


# Database object representing a static (non-feed-associated) tab.
class StaticTab(database.DDBObject):
    tabTitles = {
        'librarytab': _('My Collection'),
        'newtab': _('New Videos'),
        'searchtab': _('Search'),
        'downloadtab': _('Active Downloads'),
    }

    tabIcons = {
        'librarytab': 'collection-icon-tablist.png',
        'newtab': 'newvideos-icon-tablist.png',
        'searchtab': 'search-icon-tablist.png',
        'downloadtab': 'download-icon-tab.png',
    }

    def __init__(self, tabTemplateBase, contentsTemplate, order):
        self.tabTemplateBase = tabTemplateBase
        self.contentsTemplate = contentsTemplate
        self.order = order
        database.DDBObject.__init__(self)

    def getTitle(self):
        return self.tabTitles[self.tabTemplateBase]

    def getIconURL(self):
        return resource.url("images/%s" % self.tabIcons[self.tabTemplateBase])

    def getNumberColor(self):
        if self.tabTemplateBase == 'downloadtab':
            return 'orange'
        elif self.tabTemplateBase == 'newtab':
            return 'green'
        else:
            return None

    def getNumber(self):
        if self.tabTemplateBase == 'downloadtab':
            return views.downloadingItems.len()
        elif self.tabTemplateBase == 'newtab':
            return views.newlyDownloadedItems.len()
        else:
            return 0

class Tab:
    idCounter = 0

    def __init__(self, tabTemplateBase, contentsTemplate, sortKey, obj):
        self.tabTemplateBase = tabTemplateBase
        self.contentsTemplate = contentsTemplate
        self.sortKey = sortKey
        self.display = None
        self.id = "tab%d" % Tab.idCounter
        Tab.idCounter += 1
        self.selected = False
        self.active = False
        self.obj = obj

        if obj.__class__ == guide.ChannelGuide:
            self.type = 'guide'
        elif obj.__class__ == StaticTab: 
            self.type = 'statictab'
        elif obj.__class__ in (feed.Feed, folder.ChannelFolder): 
            self.type = 'feed'
        elif obj.__class__ in (playlist.SavedPlaylist, folder.PlaylistFolder):
            self.type = 'playlist'
        else:
            raise TypeError("Bad tab object type: %s" % type(obj))

    def setActive(self, newValue):
        self.obj.confirmDBThread()
        self.active = newValue
        self.obj.signalChange(needsSave=False)

    def setSelected(self, newValue):
        self.obj.confirmDBThread()
        self.selected = newValue
        self.obj.signalChange(needsSave=False)

    def getSelected(self):
        self.obj.confirmDBThread()
        return self.selected

    def getActive(self):
        self.obj.confirmDBThread()
        return self.active

    # Returns "normal" "selected" or "selected-inactive"
    def getState(self):
        if not self.selected:
            return 'normal'
        elif not self.active:
            return 'selected-inactive'
        else:
            return 'selected'

    def redraw(self):
        # Force a redraw by sending a change notification on the underlying
        # DB object.
        self.obj.signalChange()

    def isStatic(self):
        """True if this Tab represents a StaticTab."""
        return isinstance(self.obj, StaticTab)

    def isFeed(self):
        """True if this Tab represents a Feed."""
        return isinstance(self.obj, feed.Feed)

    def isChannelFolder(self):
        """True if this Tab represents a Channel Folder."""
        return isinstance(self.obj, folder.ChannelFolder)

    def isGuide(self):
        """True if this Tab represents a Channel Guide."""
        return isinstance(self.obj, guide.ChannelGuide)

    def isPlaylist(self):
        """True if this Tab represents a Playlist."""
        return isinstance(self.obj, playlist.SavedPlaylist)

    def isPlaylistFolder(self):
        """True if this Tab represents a Playlist Folder."""
        return isinstance(self.obj, folder.PlaylistFolder)

    def feedURL(self):
        """If this Tab represents a Feed or a Guide, the URL. Otherwise None."""
        if self.isFeed() or self.isGuide():
            return self.obj.getURL()
        else:
            return None

    def objID(self):
        """If this Tab represents a Feed, the feed's ID. Otherwise None."""
        if isinstance (self.obj, database.DDBObject):
            return self.obj.getID()
        else:
            return None

    def onDeselected(self, frame):
        self.display.onDeselect(frame)

# Remove all static tabs from the database
def removeStaticTabs():
    app.db.confirmDBThread()
    for obj in views.staticTabsObjects:
        obj.remove()

# Reload the StaticTabs in the database from the statictabs.xml resource file.
def reloadStaticTabs():
    app.db.confirmDBThread()
    # Wipe all of the StaticTabs currently in the database.
    removeStaticTabs()

    # Load them anew from the resource file.
    # NEEDS: maybe better error reporting?
    document = parse(resource.path('statictabs.xml'))
    for n in document.getElementsByTagName('statictab'):
        tabTemplateBase = n.getAttribute('tabtemplatebase')
        contentsTemplate = n.getAttribute('contentstemplate')
        order = int(n.getAttribute('order'))
        StaticTab(tabTemplateBase, contentsTemplate, order)
