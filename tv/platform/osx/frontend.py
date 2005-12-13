from PyObjCTools import NibClassBuilder, AppHelper, Conversion
from objc import YES, NO, nil
from gestalt import gestalt
from Foundation import *
from AppKit import *
from WebKit import *

try:
    from QTKit import *
except:
    print "DTV: QTKit coudln't be imported. Please upgrade to Quicktime 7 or later. You can update at http://apple.com/quicktime/ or by running Software Update."

import app
import feed
import config
import resource
import template
import database
import autoupdate

import re
import os
import sys
import objc
import time
import math
import struct
import urlparse
import threading

NibClassBuilder.extractClasses("MainMenu")
NibClassBuilder.extractClasses("MainWindow")
NibClassBuilder.extractClasses("PreferencesWindow")
NibClassBuilder.extractClasses("AddChannelSheet")
NibClassBuilder.extractClasses("PasswordWindow")
NibClassBuilder.extractClasses("FullScreenAlertPanel")

doNotCollect = {}
nc = NSNotificationCenter.defaultCenter()

def exit(returnCode):
   sys.exit(returnCode)


###############################################################################
#### Dynamically link some specific Carbon functions which we need but     ####
###  which are not available in the default MacPython                      ####
###############################################################################

kUIModeNormal = 0
kUIModeAllHidden = 3

carbonPath = objc.pathForFramework('/System/Library/Frameworks/Carbon.framework')
carbonBundle = NSBundle.bundleWithPath_(carbonPath)
objc.loadBundleFunctions(carbonBundle, globals(), ((u'SetSystemUIMode', 'III'),))

OverallAct = 0

coreServicesPath = objc.pathForFramework('/System/Library/Frameworks/CoreServices.framework')
coreServicesBundle = NSBundle.bundleWithPath_(coreServicesPath)
objc.loadBundleFunctions(coreServicesBundle, globals(), ((u'UpdateSystemActivity', 'IC'),))


###############################################################################
#### Helper methods used to display alert dialog of various types          ####
###############################################################################

def showInformationalDialog(summary, message, buttons=None):
    return showDialog(summary, message, buttons, NSInformationalAlertStyle)

def showWarningDialog(summary, message, buttons=None):
    return showDialog(summary, message, buttons, NSWarningAlertStyle)

def showCriticalDialog(summary, message, buttons=None):
    return showDialog(summary, message, buttons, NSCriticalAlertStyle)

def showDialog(summary, message, buttons, style):
    pool = NSAutoreleasePool.alloc().init()
    alert = NSAlert.alloc().init()
    alert.setAlertStyle_(style)
    alert.setMessageText_(summary)
    alert.setInformativeText_(message)
    if buttons is not None:
        for title in buttons:
            alert.addButtonWithTitle_(title)
    result = alert.runModal()
    del alert
    del pool
    return (result == NSAlertFirstButtonReturn)


###############################################################################
#### Application object                                                    ####
###############################################################################

class Application:

    def __init__(self):
        self.appl = NSApplication.sharedApplication()
        NSBundle.loadNibNamed_owner_("MainMenu", self.appl)
        controller = self.appl.delegate()
        controller.actualApp = self
        
        controller.checkQuicktimeVersion(True)

        # Force Cocoa into multithreaded mode
        # (NSThread.isMultiThreaded will be true when this call returns)
        NSThread.detachNewThreadSelector_toTarget_withObject_("noop", controller, controller)

    def Run(self):
        AppHelper.runEventLoop()

    def getBackendDelegate(self):
        return UIBackendDelegate()

    def onStartup(self):
        # For overriding
        pass

    def allowShutdown(self):
        # For overriding
        pass

    def onShutdown(self):
        # For overriding
        pass

    def addAndSelectFeed(self, url):
        # For overriding
        pass


class AppController (NibClassBuilder.AutoBaseClass):

    # Do nothing. A dummy method called by Application to force Cocoa into
    # multithreaded mode.
    def noop(self):
        return

    def applicationWillFinishLaunching_(self, notification):
        man = NSAppleEventManager.sharedAppleEventManager()
        man.setEventHandler_andSelector_forEventClass_andEventID_(
            self,
            "openURL:withReplyEvent:",
            struct.unpack(">i", "GURL")[0],
            struct.unpack(">i", "GURL")[0])
        nc.addObserver_selector_name_object_(
            self,
            'videoWillPlay:',
            'videoWillPlay',
            nil)
        nc.addObserver_selector_name_object_(
            self,
            'videoWillStop:',
            'videoWillPause',
            nil)
        nc.addObserver_selector_name_object_(
            self,
            'videoWillStop:',
            'videoWillStop',
            nil)
        
        # Call the startup hook before any events (such as instructions
        # to open files...) are delivered.
        self.actualApp.onStartup()

    def applicationDidFinishLaunching_(self, notification):
        # The [NSURLRequest setAllowsAnyHTTPSCertificate:forHost:] selector is
        # not documented anywhere, so I assume it is not public. It is however 
        # a very clean and easy way to allow us to load our channel guide from
        # https, so let's use it here anyway :)
        components = urlparse.urlparse(config.get(config.CHANNEL_GUIDE_URL))
        channelGuideHost = components[1]
        NSURLRequest.setAllowsAnyHTTPSCertificate_forHost_(YES, channelGuideHost)
        
    def applicationShouldTerminate_(self, application):
        reply = NSTerminateNow
        if not self.actualApp.allowShutdown():
            reply = NSTerminateCancel 
        return reply

    def applicationWillTerminate_(self, notification):
        # Reset the application icon to its default state
        defaultAppIcon = NSImage.imageNamed_('NSApplicationIcon')
        NSApplication.sharedApplication().setApplicationIconImage_(defaultAppIcon)
        # Call shutdown on backend
        self.actualApp.onShutdown()

    def application_openFile_(self, app, filename):
        return self.actualApp.addFeedFromFile(filename)

    def videoWillPlay_(self, notification):
        self.playPauseMenuItem.setTitle_('Pause Video')

    def videoWillStop_(self, notification):
        self.playPauseMenuItem.setTitle_('Play Video')

    def checkQuicktimeVersion(self, showError):
        supported = gestalt('qtim') >= 0x07000000
        
        if not supported and showError:
            summary = u'Unsupported version of Quicktime'
            message = u'To run DTV you need the most recent version of Quicktime, which is a free update.'
            buttons = ('Quit', 'Download Quicktime now')
            quit = showCriticalDialog(summary, message, buttons)

            if quit:
                NSApplication.sharedApplication().terminate_(nil)
            else:
                url = NSURL.URLWithString_('http://www.apple.com/quicktime/download')
                NSWorkspace.sharedWorkspace().openURL_(url)
        
        return supported

    @objc.signature('v@:@@')
    def openURL_withReplyEvent_(self, event, replyEvent):
        print "**** got open URL event"
        keyDirectObject = struct.unpack(">i", "----")[0]
        url = event.paramDescriptorForKeyword_(keyDirectObject).stringValue()

        # Convert feed: URL to http:
        # (we only get here if the URL is a feed: URL, because of what
        # we've claimed in Info.plist)
        match = re.compile(r"^feed:(.*)$").match(url)
        if match:
            url = "http:%s" % match.group(1)
            self.actualApp.addAndSelectFeed(url)

    def checkForUpdates_(self, sender):
        autoupdate.checkForUpdates(True)

    def showPreferencesWindow_(self, sender):
        prefController = PreferencesWindowController.alloc().init()
        prefController.retain()
        prefController.showWindow_(nil)

    def donate_(self, sender):
        print "NOT IMPLEMENTED"

    itemsAlwaysAvailable = ('checkForUpdates:', 'showPreferencesWindow:')
    def validateMenuItem_(self, item):
        return item.action() in self.itemsAlwaysAvailable
        

###############################################################################
#### Main window                                                           ####
###############################################################################

# ObjC classes can't be instantiated directly. To shield the user from
# this, we create a Python proxy object thats hold a reference to an
# actual ObjC class that is created when the proxy is
# instantiated. The ObjC class is in turn constructed with an 'owner'
# reference pointing to the proxy object, which is used to deliver
# callbacks.

class MainFrame:

    def __init__(self, appl):
        """The initially active display will be an instance of NullDisplay."""
        self.channelsDisplay = None
        self.collectionDisplay = None
        self.mainDisplay = None
        self.videoInfoDisplay = None
        # Do this in two steps so that self.controller is set when self.controler.init
        # is called. That way, init can turn around and call selectDisplay.
        self.controller = MainController.alloc()
        self.controller.init(self, appl)

    def selectDisplay(self, display, area=None):
        """Install the provided 'display' in the requested area"""
        self.controller.selectDisplay(display, area)

    # Internal use: return an estimate of the size of a given display area as
    # a Cocoa frame object.
    def getDisplaySizeHint(self, area):
        return self.controller.getDisplaySizeHint(area)


class DisplayHostView (NibClassBuilder.AutoBaseClass):
    
    def awakeFromNib(self):
        self.scheduledDisplay = None
        self.hostedDisplay = None
        self.hostedView = nil

    def drawRect_(self, rect):
        NSColor.whiteColor().set()
        NSRectFill(rect)

    def setScheduledDisplay(self, display):
        if self.scheduledDisplay is not None:
            self.scheduledDisplay.cancel()
        self.scheduledDisplay = display
        
    def setDisplay(self, display, owner):
        pool = NSAutoreleasePool.alloc().init()

        self.scheduledDisplay = None

        # Send notification to old display if any
        if self.hostedDisplay is not None:
            self.hostedDisplay.onDeselected_private(owner)
            self.hostedDisplay.onDeselected(owner)
        oldView = self.hostedView

        # Switch to new display
        self.hostedDisplay = display
        if display is not None:
            self.hostedView = display.getView()
        else:
            self.hostedView = nil
        if display is None:
            return

        # Figure out where to put the content area
        # NEEDS: clean up outlet names/types in nib
        frame = self.bounds()
        mask = self.autoresizingMask()

        # Arrange to cover the template that marks the content area
        self.hostedView.setFrame_(frame)
        self.addSubview_(self.hostedView)
        self.hostedView.setAutoresizingMask_(mask)

        # Mark as needing display
        self.setNeedsDisplayInRect_(frame)
        self.hostedView.setNeedsDisplay_(YES)

        # Wait until now to clean up the old view, to reduce flicker
        # (doesn't actually work all that well, sadly -- possibly what
        # we want to do is wait until notification comes from the new
        # view that it's been fully loaded to even show it)
        if oldView and (not (oldView is self.hostedView)):
            oldView.removeFromSuperview()

        # Send notification to new display
        display.onSelected_private(owner)
        display.onSelected(owner)
        
        del pool


class MainController (NibClassBuilder.AutoBaseClass):

    def init(self, frame, appl):
        NSObject.init(self)
        self.frame = frame
        self.appl = appl
        NSBundle.loadNibNamed_owner_("MainWindow", self)

        nc.addObserver_selector_name_object_(
            self,
            'appWillTerminate:',
            NSApplicationWillTerminateNotification,
            NSApplication.sharedApplication())

        return self

    def awakeFromNib(self):
        self.frame.channelsDisplay = self.channelsHostView
        self.frame.collectionDisplay = None #self.collectionHostView
        self.frame.mainDisplay = self.mainHostView
        self.frame.videoInfoDisplay = self.videoInfoHostView
        self.restoreLayout()
        self.updateWindowTexture()
        self.actionButton.sendActionOn_(NSLeftMouseDownMask)
        self.showWindow_(nil)

    def appWillTerminate_(self, notification):
        self.saveLayout()

    def restoreLayout(self):
        windowFrame = config.get(config.MAIN_WINDOW_FRAME)
        if windowFrame is None:
            windowFrame = self.window().frame()
        else:
            windowFrame = NSRectFromString(windowFrame)
        screen = self.window().screen()
        if screen is not None:
            visibleFrame = screen.visibleFrame()
            if not NSContainsRect(visibleFrame, windowFrame):
                print "DTV: Fitting window to screen size"
                windowFrame = visibleFrame
        self.window().setFrame_display_(windowFrame, NO)

        leftFrame = config.get(config.LEFT_VIEW_SIZE)
        rightFrame = config.get(config.RIGHT_VIEW_SIZE)
        if leftFrame is not None and rightFrame is not None:
           leftFrame = NSRectFromString(leftFrame)
           rightFrame = NSRectFromString(rightFrame)
           self.splitView.subviews().objectAtIndex_(0).setFrame_(leftFrame)
           self.splitView.subviews().objectAtIndex_(1).setFrame_(rightFrame)
           self.splitView.adjustSubviews()

    def saveLayout(self):
        windowFrame = self.window().frame()
        windowFrame = NSStringFromRect(windowFrame)
        leftFrame = self.splitView.subviews().objectAtIndex_(0).frame()
        leftFrame = NSStringFromRect(leftFrame)
        rightFrame = self.splitView.subviews().objectAtIndex_(1).frame()
        rightFrame = NSStringFromRect(rightFrame)

        config.set(config.MAIN_WINDOW_FRAME, windowFrame)
        config.set(config.LEFT_VIEW_SIZE, leftFrame)
        config.set(config.RIGHT_VIEW_SIZE, rightFrame)
        config.save()

    def updateWindowTexture(self):
        bgTexture = NSImage.alloc().initWithSize_(self.window().frame().size)
        bgTexture.lockFocus()
                
        topImage = NSImage.imageNamed_(u'wtexture_top')
        topColor = NSColor.colorWithPatternImage_(topImage)
        topColor.set()
        NSGraphicsContext.currentContext().setPatternPhase_(bgTexture.size())
        NSRectFill(((0, bgTexture.size().height - topImage.size().height), (bgTexture.size().width, topImage.size().height)))
        
        bottomImage = NSImage.imageNamed_(u'wtexture_bottom')
        bottomColor = NSColor.colorWithPatternImage_(bottomImage)
        bottomColor.set()
        NSGraphicsContext.currentContext().setPatternPhase_(bottomImage.size())
        NSRectFill(((0, 0), (bgTexture.size().width, bottomImage.size().height)))

        bgColor = NSColor.colorWithCalibratedWhite_alpha_(195.0/255.0, 1.0)
        bgColor.set()
        NSRectFill(((0, bottomImage.size().height), (bgTexture.size().width, bgTexture.size().height -  bottomImage.size().height - topImage.size().height)))
        
        bgTexture.unlockFocus()
        
        self.window().setBackgroundColor_(NSColor.colorWithPatternImage_(bgTexture))
        
    ### Switching displays ###

    def selectDisplay(self, display, area):
        if display is not None:
            # Tell the display area that the next display it will host once it's
            # ready is this one.
            area.setScheduledDisplay(display)
            # Tell the new display we want to switch to it. It'll call us
            # back when it's ready to display without flickering.
            display.callWhenReadyToDisplay(lambda: self.doSelectDisplay(display, area))

    def doSelectDisplay(self, display, area):
        if area is not None:
            area.setDisplay(display, self.frame)
            if isinstance(display, app.TemplateDisplay) and area == self.mainHostView:
                view = display.getWatchable()
                if view is not None:
                    nc.postNotificationName_object_userInfo_('displayIsWatchable', display, {'view': view})
                else:
                    nc.postNotificationName_object_('displayIsNotWatchable', display)

    def getDisplaySizeHint(self, area):
        return area.frame()

    ### Window resize handler

    def windowDidResize_(self, notification):
        self.updateWindowTexture()

    ### Size constraints on splitview ###

    minimumTabListWidth = 160 # pixels
    minimumContentWidth = 500 # pixels

    # How far left can the user move the slider?
    def splitView_constrainMinCoordinate_ofSubviewAt_(self, sender, proposedMin, offset):
        return proposedMin + self.minimumTabListWidth

    # How far right can the user move the slider?
    def splitView_constrainMaxCoordinate_ofSubviewAt_(self, sender, proposedMax, offset):
        return proposedMax - self.minimumContentWidth

    # The window was resized; compute new positions of the splitview
    # children. Rule: resizing the window doesn't change the size of
    # the tab list unless it's necessary to shrink it to obey the
    # minimum content area size constraint.
    def splitView_resizeSubviewsWithOldSize_(self, sender, oldSize):
        tabBox = self.channelsHostView.superview()
        contentBox = self.mainHostView.superview()

        splitViewSize = sender.frame().size
        tabSize = tabBox.frame().size
        contentSize = contentBox.frame().size
        dividerWidth = sender.dividerThickness()

        tabSize.height = contentSize.height = splitViewSize.height

        contentSize.width = splitViewSize.width - dividerWidth - tabSize.width
        if contentSize.width < self.minimumContentWidth:
            contentSize.width = self.minimumContentWidth
        tabSize.width = splitViewSize.width - dividerWidth - contentSize.width

        tabBox.setFrameSize_(tabSize)
        tabBox.setFrameOrigin_(NSZeroPoint)
        contentBox.setFrameSize_(contentSize)
        contentBox.setFrameOrigin_((tabSize.width + dividerWidth, 0))

    def splitView_canCollapseSubview_(self, sender, subview):
        return self.channelsHostView.isDescendantOf_(subview) and app.Controller.instance.videoDisplay.isSelected()

    ### Actions ###

    def switchTabs_(self, sender):
        tag = sender.selectedCell().tag()
        self.tabView.selectTabViewItemAtIndex_(tag)
        newDisplay = None
        if tag == 1:
            newDisplay = self.frame.channelsDisplay
        elif tag == 2:
            newDisplay = self.frame.collectionDisplay
        self.appl.onDisplaySwitch(newDisplay)

    def playPause_(self, sender):
        VideoDisplayController.getInstance().playPause_(sender)

    def stopVideo_(self, sender):
        VideoDisplayController.getInstance().stop_(sender)

    def playFullScreen_(self, sender):
        VideoDisplayController.getInstance().playFullScreen_(sender)

    def playHalfScreen_(self, sender):
        pass

    def deleteVideo_(self, sender):
        print "NOT IMPLEMENTED"

    def saveVideo_(self, sender):
        print "NOT IMPLEMENTED"

    def copyVideoLink_(self, sender):
        print "NOT IMPLEMENTED"

    def addChannel_(self, sender):
        controller = AddChannelSheetController.alloc().init(self.appl)
        controller.retain()
        NSApplication.sharedApplication().beginSheet_modalForWindow_modalDelegate_didEndSelector_contextInfo_(controller.window(), self.window(), self, nil, 0)

    def removeChannel_(self, sender):
        feedID = app.Controller.instance.currentSelectedTab.feedID()
        if feedID is not None:
            backEndDelegate = self.appl.getBackendDelegate()
            app.ModelActionHandler(backEndDelegate).removeFeed(feedID)

    def copyChannelLink_(self, sender):
        NSPasteboard.generalPasteboard().declareTypes_owner_([NSURLPboardType], self)

    def updateChannel_(self, sender):
        feedID = app.Controller.instance.currentSelectedTab.feedID()
        if feedID is not None:
            backEndDelegate = self.appl.getBackendDelegate()
            app.ModelActionHandler(backEndDelegate).updateFeed(feedID)

    def updateAllChannels_(self, sender):
        print "NOT IMPLEMENTED"

    def renameChannel_(self, sender):
        print "NOT IMPLEMENTED"

    def createCollection_(self, sender):
        print "NOT IMPLEMENTED"

    def deleteCollection_(self, sender):
        print "NOT IMPLEMENTED"

    def sendCollectionToFriend_(self, sender):
        print "NOT IMPLEMENTED"

    def tellAFriend_(self, sender):
        print "NOT IMPLEMENTED"

    def showActionMenu_(self, sender):
        mainMenu = NSApplication.sharedApplication().mainMenu()
        # collections do not really work just yet so we hardcode the action menu
        # to the channel menu
        # tag = self.switcherMatrix.selectedCell().tag()
        tag = 1
        menu = mainMenu.itemWithTag_(tag).submenu()

        location = sender.frame().origin
        location.x += 10.0
        location.y += 3.0

        curEvent = NSApplication.sharedApplication().currentEvent()
        event = NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
            curEvent.type(),
            location,
            curEvent.modifierFlags(),
            curEvent.timestamp(),
            curEvent.windowNumber(),
            nil,
            curEvent.eventNumber(),
            curEvent.clickCount(),
            curEvent.pressure() )

        NSMenu.popUpContextMenu_withEvent_forView_( menu, event, sender )

    def showHelp_(self, sender):
        summary = u'Help for DTV will be available soon.'
        message = u'In the meantime, please visit our homepage for our help FAQ: http://participatoryculture.org/\n\nFor individual user support, please e-mail feedback@ppolitics.org.'
        showInformationalDialog(summary, message)

    itemsAlwaysAvailable = ('addChannel:', 'showHelp:')
    selectedChannelItems = ('removeChannel:', 'copyChannelLink:', 'updateChannel:')
    def validateMenuItem_(self, item):
        if item.action() in self.selectedChannelItems:
            currentTab = app.Controller.instance.currentSelectedTab
            return currentTab is not None and currentTab.isFeed()
        elif item.action() == 'playPause:' or item.action() == 'playFullScreen:':
            display = self.frame.mainDisplay.hostedDisplay
            if display is not None:
                if display is app.Controller.instance.videoDisplay:
                    return YES
                else:
                    return display.getWatchable() is not None
            else:
                return NO
        elif item.action() == 'stopVideo:':
            return self.frame.mainDisplay.hostedDisplay is app.Controller.instance.videoDisplay
        else:
            return item.action() in self.itemsAlwaysAvailable

    def pasteboard_provideDataForType_(self, pasteboard, type):
        feedURL = app.Controller.instance.currentSelectedTab.feedURL()
        if feedURL is not None:
            url = NSURL.URLWithString_(feedURL)
            url.writeToPasteboard_(NSPasteboard.generalPasteboard())


###############################################################################
#### Fullscreen alert panel                                                ####
###############################################################################

class FullScreenAlertPanelController (NibClassBuilder.AutoBaseClass):

    @classmethod
    def displayIfNeeded(cls):
        noAlert = config.get(config.NO_FULLSCREEN_ALERT)
        if not noAlert:
            controller = FullScreenAlertPanelController.alloc().init()
            NSApplication.sharedApplication().runModalForWindow_(controller.window())

    def init(self):
        parent = super(FullScreenAlertPanelController, self)
        self = parent.initWithWindowNibName_owner_('FullScreenAlertPanel', self)
        return self

    def dismiss_(self, sender):
        if self.dontShowCheckbox.state() == NSOnState:
            config.set(config.NO_FULLSCREEN_ALERT, True)
        NSApplication.sharedApplication().stopModal()
        self.window().orderOut_(nil)


###############################################################################
#### Add channel sheet                                                     ####
###############################################################################

class AddChannelSheetController (NibClassBuilder.AutoBaseClass):

    def init(self, parent):
        super(AddChannelSheetController, self).initWithWindowNibName_owner_("AddChannelSheet", self)
        self.parentController = parent
        return self

    def awakeFromNib(self):
        url = NSPasteboard.generalPasteboard().stringForType_(NSStringPboardType)
        if url is None or not feed.validateFeedURL(url):
            url = ''
        self.addChannelSheetURL.setStringValue_(url)

    def addChannelSheetDone_(self, sender):
        sheetURL = self.addChannelSheetURL.stringValue()
        self.parentController.addAndSelectFeed(sheetURL)
        self.closeSheet()

    def addChannelSheetCancel_(self, sender):
        self.closeSheet()

    def closeSheet(self):
        NSApplication.sharedApplication().endSheet_(self.window())
        self.window().orderOut_(self)
        #self.release()


###############################################################################
#### Preferences window                                                    ####
###############################################################################

class PreferencesWindowController (NibClassBuilder.AutoBaseClass):

    class PreferenceItem (NSToolbarItem):
        def setView_(self, view):
            self.view = view

    def init(self):
        super(PreferencesWindowController, self).initWithWindowNibName_("PreferencesWindow")
        return self

    def awakeFromNib(self):
        generalItem = self.makePreferenceItem("GeneralItem", "General", "general_pref", self.generalView)
        channelsItem = self.makePreferenceItem("ChannelsItem", "Channels", "channels_pref", self.channelsView)
        downloadsItem = self.makePreferenceItem("DownloadsItem", "Downloads", "downloads_pref", self.downloadsView)
        diskSpaceItem = self.makePreferenceItem("DiskSpaceItem", "Disk Space", "disk_space_pref", self.diskSpaceView)

        self.items = {generalItem.itemIdentifier(): generalItem,
                      channelsItem.itemIdentifier(): channelsItem,
                      downloadsItem.itemIdentifier(): downloadsItem,
                      diskSpaceItem.itemIdentifier(): diskSpaceItem}

        self.allItems = (generalItem.itemIdentifier(),
                         channelsItem.itemIdentifier(),
                         downloadsItem.itemIdentifier(),
                         diskSpaceItem.itemIdentifier())

        initialItem = generalItem

        toolbar = NSToolbar.alloc().initWithIdentifier_("Preferences")
        toolbar.setDelegate_(self)
        toolbar.setAllowsUserCustomization_(NO)
        toolbar.setSelectedItemIdentifier_(initialItem.itemIdentifier())

        self.window().setToolbar_(toolbar)
        if hasattr(self.window(), 'setShowsToolbarButton_'): # 10.4 only
            self.window().setShowsToolbarButton_(NO)
        self.switchPreferenceView_(initialItem)

    def makePreferenceItem(self, identifier, label, imageName, view):
        item = self.PreferenceItem.alloc().initWithItemIdentifier_(identifier)
        item.setLabel_(label)
        item.setImage_(NSImage.imageNamed_(imageName))
        item.setTarget_(self)
        item.setAction_("switchPreferenceView:")
        item.setView_(view)
        return item

    def toolbarAllowedItemIdentifiers_(self, toolbar):
        return self.allItems

    def toolbarDefaultItemIdentifiers_(self, toolbar):
        return self.allItems

    def toolbarSelectableItemIdentifiers_(self, toolbar):
        return self.allItems

    def toolbar_itemForItemIdentifier_willBeInsertedIntoToolbar_(self, toolbar, itemIdentifier, flag ):
        return self.items[ itemIdentifier ]

    def validateToolbarItem_(self, item):
        return YES

    def switchPreferenceView_(self, sender):
        if self.window().contentView() == sender.view:
            return

        window = self.window()
        wframe = window.frame()
        vframe = sender.view.frame()
        toolbarHeight = wframe.size.height - window.contentView().frame().size.height
        wframe.origin.y += wframe.size.height - vframe.size.height - toolbarHeight
        wframe.size = vframe.size
        wframe.size.height += toolbarHeight

        self.window().setContentView_(sender.view)
        self.window().setFrame_display_animate_(wframe, YES, YES)

class GeneralPrefsController (NibClassBuilder.AutoBaseClass):
    
    def awakeFromNib(self):
        run = config.get(config.RUN_DTV_AT_STARTUP)
        self.runAtStartupCheckBox.setState_(run and NSOnState or NSOffState)
    
    def runAtStartup_(self, sender):
        run = (sender.state() == NSOnState)
        config.set(config.RUN_DTV_AT_STARTUP, run)

        defaults = NSUserDefaults.standardUserDefaults()
        lwdomain = defaults.persistentDomainForName_('loginwindow')
        lwdomain = Conversion.pythonCollectionFromPropertyList(lwdomain)
        launchedApps = lwdomain['AutoLaunchedApplicationDictionary']
        ourPath = NSBundle.mainBundle().bundlePath()
        ourEntry = None
        for entry in launchedApps:
            if entry['Path'] == ourPath:
                ourEntry = entry
                break

        if run and ourEntry is None:
            launchInfo = dict(Path=ourPath, Hide=NO)
            launchedApps.append(launchInfo)
        elif ourEntry is not None:
            launchedApps.remove(entry)

        lwdomain = Conversion.propertyListFromPythonCollection(lwdomain)
        defaults.setPersistentDomain_forName_(lwdomain, 'loginwindow')
        defaults.synchronize()
                    
class ChannelsPrefsController (NibClassBuilder.AutoBaseClass):

    def awakeFromNib(self):
        minutes = config.get(config.CHECK_CHANNELS_EVERY_X_MN)
        self.periodicityPopup.selectItemWithTag_(minutes)

    def checkEvery_(self, sender):
        minutes = sender.selectedItem().tag()
        config.set(config.CHECK_CHANNELS_EVERY_X_MN, minutes)

class DownloadsPrefsController (NibClassBuilder.AutoBaseClass):
    
    def awakeFromNib(self):
        limit = config.get(config.LIMIT_UPSTREAM)
        self.limitUpstreamCheckBox.setState_(limit and NSOnState or NSOffState)
        self.limitValueField.setEnabled_(limit)
        self.limitValueField.setIntValue_(config.get(config.UPSTREAM_LIMIT_IN_KBS))
    
    def limitUpstream_(self, sender):
        limit = (sender.state() == NSOnState)
        self.limitValueField.setEnabled_(limit)
        config.set(config.LIMIT_UPSTREAM, limit)
        self.setUpstreamLimit_(self.limitValueField)
    
    def setUpstreamLimit_(self, sender):
        limit = sender.floatValue()
        config.set(config.UPSTREAM_LIMIT_IN_KBS, limit)

class DiskSpacePrefsController (NibClassBuilder.AutoBaseClass):
    
    def awakeFromNib(self):
        preserve = config.get(config.PRESERVE_DISK_SPACE)
        self.preserveSpaceCheckBox.setState_(preserve and NSOnState or NSOffState)
        self.minimumSpaceField.setEnabled_(preserve)
        self.minimumSpaceField.setIntValue_(config.get(config.PRESERVE_X_GB_FREE))
        self.expirationDelayPopupButton.selectItemWithTag_(int(config.get(config.EXPIRE_AFTER_X_DAYS) * 24))
    
    def preserveDiskSpace_(self, sender):
        preserve = (sender.state() == NSOnState)
        self.minimumSpaceField.setEnabled_(preserve)
        config.set(config.PRESERVE_DISK_SPACE, preserve)
        self.setMinimumSpace_(self.minimumSpaceField)
    
    def setMinimumSpace_(self, sender):
        space = sender.floatValue()
        config.set(config.PRESERVE_X_GB_FREE, space)
        
    def setExpirationDelay_(self, sender):
        delay = sender.selectedItem().tag()
        config.set(config.EXPIRE_AFTER_X_DAYS, delay / 24.0)


###############################################################################
#### 'Delegate' objects for asynchronously asking the user questions       ####
###############################################################################

class UIBackendDelegate:

    def getHTTPAuth(self, url, domain, prefillUser = None, prefillPassword = None):
        """Ask the user for HTTP login information for a location, identified
        to the user by its URL and the domain string provided by the
        server requesting the authorization. Default values can be
        provided for prefilling the form. If the user submits
        information, it's returned as a (user, password)
        tuple. Otherwise, if the user presses Cancel or similar, None
        is returned."""
        message = "%s requires a username and password for \"%s\"." % (url, domain)
        return PasswordController.alloc().init(message, prefillUser, prefillPassword).getAnswer()

    def isScrapeAllowed(self, url):
        """Tell the user that URL wasn't a valid feed and ask if it should be
        scraped for links instead. Returns True if the user gives
        permission, or False if not."""
        summary = u'Non-Standard Channel'
        message = u'%s is not a DTV-style channel. DTV can try to subscribe, but videos may lack proper descriptions and thumbnails.\nPlease notify the publisher if you want this channel to be fully supported\n\nContinue ?' % url
        buttons = (u'Subscribe', u'Cancel')
        return showWarningDialog(summary, message, buttons)

    def updateAvailable(self, url):
        """Tell the user that an update is available and ask them if they'd
        like to download it now"""
        summary = u'DTV Version Alert'
        message = u'A new version of DTV is available.\n\nWould you like to download it now?'
        buttons = (u'Download', u'Cancel')
        download = showInformationalDialog(summary, message, buttons)
        if download:
            self.openExternalURL(url)

    def dtvIsUpToDate(self):
        summary = u'DTV Version Check'
        message = u'This version of DTV is up to date.'
        showInformationalDialog(summary, message)

    def saveFailed(self, reason):
        summary = u'DTV database save failed'
        message = u"DTV was unable to save its database.\nRecent changes may be lost\n\n%s" % reason
        buttons = (u'Continue',)
        return showCriticalDialog(summary, message, buttons)

    def validateFeedRemoval(self, feedTitle):
        summary = u'Remove Channel'
        message = u'Are you sure you want to remove the channel \'%s\'? This operation cannot be undone.' % feedTitle
        buttons = (u'Remove', u'Cancel')
        return showCriticalDialog(summary, message, buttons)

    def openExternalURL(self, url):
        # We could use Python's webbrowser.open() here, but
        # unfortunately, it doesn't have the same semantics under UNIX
        # as under other OSes. Sometimes it blocks, sometimes it doesn't.
        pool = NSAutoreleasePool.alloc().init()
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))
        del pool

    def updateAvailableItemsCountFeedback(self, count):
        pool = NSAutoreleasePool.alloc().init()
        appIcon = NSImage.imageNamed_('NSApplicationIcon')
        badgedIcon = NSImage.alloc().initWithSize_(appIcon.size())
        badgedIcon.lockFocus()
        try:
            appIcon.compositeToPoint_operation_((0,0), NSCompositeSourceOver)
            if count > 0:
                digits = len(str(count))
                badge = nil
                if digits <= 2:
                    badge = NSImage.imageNamed_('dock_badge_1_2.png')
                elif digits <= 5:
                    badge = NSImage.imageNamed_('dock_badge_%d.png' % digits)
                else:
                    print "DTV: Wow, that's a whole lot of new items!"
                if badge is not nil:
                    appIconSize = appIcon.size()
                    badgeSize = badge.size()
                    badgeLoc = (appIconSize.width - badgeSize.width, appIconSize.height - badgeSize.height)
                    badge.compositeToPoint_operation_(badgeLoc, NSCompositeSourceOver)
                    badgeLabel = NSString.stringWithString_(u'%d' % count)
                    badgeLabelFont = NSFont.boldSystemFontOfSize_(24)
                    badgeLabelColor = NSColor.whiteColor()
                    badgeParagraphStyle = NSMutableParagraphStyle.alloc().init()
                    badgeParagraphStyle.setAlignment_(NSCenterTextAlignment)
                    badgeLabelAttributes = {NSFontAttributeName: badgeLabelFont, 
                                            NSForegroundColorAttributeName: badgeLabelColor,
                                            NSParagraphStyleAttributeName: badgeParagraphStyle}
                    badgeLabelLoc = (badgeLoc[0], badgeLoc[1]-10)
                    badgeLabel.drawInRect_withAttributes_((badgeLabelLoc, badgeSize), badgeLabelAttributes)
        finally:
            badgedIcon.unlockFocus()
        NSApplication.sharedApplication().setApplicationIconImage_(badgedIcon)
        del pool
        
    def interruptDownloadsAtShutdown(self, downloadsCount):
        summary = u'Are you sure you want to quit?'
        message = u'You have %d download%s still in progress.' % (downloadsCount, downloadsCount > 1 and 's' or '')
        buttons = (u'Quit', u'Cancel')
        return showWarningDialog(summary, message, buttons)


class PasswordController (NibClassBuilder.AutoBaseClass):

    def init(self, message, prefillUser = None, prefillPassword = None):
        pool = NSAutoreleasePool.alloc().init()
        # sets passwordField, textArea, usernameField, window
        NSBundle.loadNibNamed_owner_("PasswordWindow", self)

        self.usernameField.setStringValue_(prefillUser or "")
        self.passwordField.setStringValue_(prefillPassword or "")
        self.textArea.setStringValue_(message)
        self.result = None
        self.condition = threading.Condition()

        # Ensure we're not deallocated until the window that has actions
        # that point at us is closed
        self.retain()
        del pool
        return self

    def getAnswer(self):
        """Present the dialog and wait for user answer. Returns (username,
        password) if the user pressed OK, or None if the user pressed Cancel."""
        # PasswordController is likely to get release()d by Python in response
        # to getAnswer returning.
        self.performSelectorOnMainThread_withObject_waitUntilDone_("showAtModalLevel:", nil, NO)
        self.condition.acquire()
        self.condition.wait()
        self.condition.release()
        self.release()
        return self.result

    # executes in GUI thread
    def showAtModalLevel_(self, sender):
        self.window.setLevel_(NSModalPanelWindowLevel)
        self.window.makeKeyAndOrderFront_(nil)

    # bound to button in nib
    def acceptEntry_(self, sender):
        self.condition.acquire()
        self.result = (self.usernameField.stringValue(),
        self.passwordField.stringValue())
        self.window.close()
        self.condition.notify()
        self.condition.release()

    # bound to button in nib
    def cancelEntry_(self, sender):
        self.condition.acquire()
        self.result = None
        self.window.close()
        self.condition.notify()
        self.condition.release()


###############################################################################
#### The tab selector button class                                         ####
###############################################################################

class TabButtonCell (NibClassBuilder.AutoBaseClass):

    def awakeFromNib(self):
        self.background = NSImage.imageNamed_('tab')
        self.selectedBackground = NSImage.imageNamed_('tab_blue')

        pstyle = NSMutableParagraphStyle.alloc().init()
        pstyle.setAlignment_(NSCenterTextAlignment)
        self.titleAttrs = { NSFontAttributeName:NSFont.fontWithName_size_("Lucida Grande", 12),
                            NSParagraphStyleAttributeName:pstyle }

    def drawInteriorWithFrame_inView_(self, rect, view):
        image = self.background
        if self.state() == NSOnState:
            image = self.selectedBackground

        y = rect.origin.y
        if view.isFlipped():
            y += rect.size.height

        tileWidth = image.size().width
        times = math.ceil(rect.size.width / tileWidth)
        for i in range(0, int(times)):
            x = (rect.origin.x + (i * tileWidth), y)
            image.compositeToPoint_operation_(x, NSCompositeSourceOver)

        self.drawTitle(rect)

    def drawTitle(self, rect):
        r = rect
        r.origin.x += 8
        r.origin.y += 5
        r.size.width -= 16
        title = self.title()
        NSString.stringWithString_(title).drawInRect_withAttributes_( r, self.titleAttrs )


###############################################################################
#### Custom split view                                                     ####
###############################################################################

class DTVSplitView (NibClassBuilder.AutoBaseClass):
    
    def awakeFromNib(self):
        self.background = NSImage.imageNamed_('splitview_divider_background')
        self.backgroundRect = ((0,0), self.background.size())
        self.dimple = NSImage.imageNamed_('splitview_divider_dimple')
        
    def dividerThickness(self):
        return 10.0
        
    def drawDividerInRect_(self, rect):
        dividerOrigin = (rect.origin.x, 12)
        dividerSize = (rect.size.width, rect.size.height - 58 - 12)
        dividerRect = (dividerOrigin, dividerSize)
        self.background.drawInRect_fromRect_operation_fraction_(dividerRect, self.backgroundRect, NSCompositeSourceOver, 1.0)
        dimplePosition = (rect.origin.x, (dividerSize[1] - self.dimple.size().height) / 2)
        self.dimple.compositeToPoint_operation_(dimplePosition, NSCompositeSourceOver)


###############################################################################
#### Custom metal slider                                                   ####
###############################################################################

class MetalSlider (NibClassBuilder.AutoBaseClass):

    def awakeFromNib(self):
        oldCell = self.cell()
        newCell = MetalSliderCell.alloc().init()
        newCell.setState_(oldCell.state())
        newCell.setEnabled_(oldCell.isEnabled())
        newCell.setFloatValue_(oldCell.floatValue())
        newCell.setTarget_(oldCell.target())
        newCell.setAction_(oldCell.action())
        self.setCell_(newCell)


class MetalSliderCell (NSSliderCell):

    def init(self):
        self = super(MetalSliderCell, self).init()
        self.knob = NSImage.imageNamed_('volume_knob')
        self.knobPressed = NSImage.imageNamed_('volume_knob_blue')
        self.knobSize = self.knob.size()
        return self

    def knobRectFlipped_(self, flipped):
        value = self.floatValue()
        course = self.controlView().bounds().size.width - self.knobSize.width
        origin = NSPoint(course * value, 0)
        return ( origin, (self.knobSize.width, self.controlView().bounds().size.height) )

    def drawKnob_(self, rect):
        self.controlView().lockFocus()
        location = NSPoint(rect.origin.x, rect.origin.y + rect.size.height + 1)
        if self.isEnabled():
            self.knob.compositeToPoint_operation_(location, NSCompositeSourceOver)
        else:
            self.knob.dissolveToPoint_fraction_(location, 0.5)
        self.controlView().unlockFocus()


###############################################################################
#### Our own prettier beveled NSBox                                        ####
###############################################################################

#class BeveledBox (NibClassBuilder.AutoBaseClass):
#    # Actual base class is NSBox
#
#    TOP_COLOR        = NSColor.colorWithDeviceWhite_alpha_( 147 / 255.0, 1.0 )
#    LEFT_RIGHT_COLOR = NSColor.colorWithDeviceWhite_alpha_( 224 / 255.0, 1.0 )
#    BOTTOM_COLOR     = NSColor.colorWithDeviceWhite_alpha_( 240 / 255.0, 1.0 )
#    CONTOUR_COLOR    = NSColor.colorWithDeviceWhite_alpha_( 102 / 255.0, 1.0 )
#
#    def drawRect_(self, rect):
#        interior = NSInsetRect( rect, 1, 1 )
#
#        NSColor.whiteColor().set()
#        NSRectFill( interior )
#
#        self.CONTOUR_COLOR.set()
#        NSFrameRect( interior )
#
#        self.TOP_COLOR.set()
#        p1 = NSPoint( rect.origin.x+1, rect.size.height-0.5 )
#        p2 = NSPoint( rect.size.width-1, rect.size.height-0.5 )
#        NSBezierPath.strokeLineFromPoint_toPoint_( p1, p2 )
#
#        self.LEFT_RIGHT_COLOR.set()
#        p1 = NSPoint( rect.origin.x+0.5, rect.size.height-1 )
#        p2 = NSPoint( rect.origin.x+0.5, rect.origin.y+1 )
#        NSBezierPath.strokeLineFromPoint_toPoint_( p1, p2 )
#        p1 = NSPoint( rect.size.width-0.5, rect.origin.y+1 )
#        p2 = NSPoint( rect.size.width-0.5, rect.size.height-1 )
#        NSBezierPath.strokeLineFromPoint_toPoint_( p1, p2 )
#
#        self.BOTTOM_COLOR.set()
#        p1 = NSPoint( rect.origin.x+1, rect.origin.y+0.5 )
#        p2 = NSPoint( rect.size.width-1, rect.origin.y+0.5 )
#        NSBezierPath.strokeLineFromPoint_toPoint_( p1, p2 )


###############################################################################
#### The progress display                                                  ####
###############################################################################

class ProgressDisplayView (NibClassBuilder.AutoBaseClass):
    # Actual base class is NSView

    def initWithFrame_(self, frame):
        super(ProgressDisplayView, self).initWithFrame_(frame)

        self.backgroundLeft = NSImage.imageNamed_( "display_left" )
        self.backgroundLeftWidth = self.backgroundLeft.size().width
        self.backgroundRight = NSImage.imageNamed_( "display_right" )
        self.backgroundRightWidth = self.backgroundRight.size().width
        self.backgroundCenter = NSImage.imageNamed_( "display_center" )
        self.backgroundCenterWidth = self.backgroundCenter.size().width

        self.grooveContourColor = NSColor.colorWithCalibratedWhite_alpha_( 0.1, 0.3 )
        self.grooveFillColor = NSColor.colorWithCalibratedWhite_alpha_( 0.5, 0.3 )

        self.timeAttrs = { NSFontAttributeName:NSFont.fontWithName_size_("Helvetica", 10),
                           NSForegroundColorAttributeName:NSColor.colorWithCalibratedWhite_alpha_(69/255.0, 1.0) }

        self.cursor = NSImage.alloc().initWithSize_((10,10))
        self.cursor.lockFocus()
        path = NSBezierPath.bezierPath()
        path.moveToPoint_((0, 4.5))
        path.lineToPoint_((4, 8))
        path.lineToPoint_((8, 4.5))
        path.lineToPoint_((4, 1))
        path.closePath()
        NSColor.colorWithCalibratedWhite_alpha_( 51/255.0, 1.0 ).set()
        path.fill()
        self.cursor.unlockFocus()
        
        self.renderer = None
        self.dragging = False
        self.updateTimer = nil

        return self;

    def setup(self, renderer):
        if self.renderer != renderer:
            self.renderer = renderer
            if renderer is not nil:
                self.updateTimer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1.0, self, 'refresh:', nil, YES)
            elif self.updateTimer is not nil:
                self.updateTimer.invalidate()
                self.updateTimer = nil
            self.refresh_(nil)

    def teardown(self):
        self.setup(None)

    def refresh_(self, timer):
        self.setNeedsDisplay_(YES)

    def mouseDown_(self, event):
        if self.renderer is not None:
            location = self.convertPoint_fromView_(event.locationInWindow(), nil)
            if NSPointInRect(location, self.getGrooveRect()):
                self.dragging = True
                self.renderer.stop()
                self.renderer.setCurrentTime(self.getTimeForLocation(location))
                self.refresh_(nil)

    def mouseDragged_(self, event):
        if self.dragging:
            location = self.convertPoint_fromView_(event.locationInWindow(), nil)
            self.renderer.setCurrentTime(self.getTimeForLocation(location))
            self.refresh_(nil)

    def mouseUp_(self, event):
        if self.renderer is not None:
            self.dragging = False
            self.renderer.play()
            self.refresh_(nil)

    def getTimeForLocation(self, location):
        rect = self.getGrooveRect()
        offset = location.x - rect.origin.x
        if offset < 0:
            offset = 0
        if offset > rect.size.width:
            offset = rect.size.width
        if offset > 0:
            offset /= rect.size.width
        time = self.renderer.getDuration() * offset
        return time

    def drawRect_(self, rect):
        self.drawBackground()
        self.drawTimeIndicator()
        self.drawProgressGroove()
        self.drawProgressCursor()

    def drawBackground(self):
        self.backgroundLeft.compositeToPoint_operation_( (0,0), NSCompositeSourceOver )

        x = self.bounds().size.width - self.backgroundRightWidth
        self.backgroundRight.compositeToPoint_operation_( (x, 0), NSCompositeSourceOver )

        emptyWidth = self.bounds().size.width - (self.backgroundRightWidth + self.backgroundLeftWidth)
        emptyRect = ((self.backgroundLeftWidth, 0), (emptyWidth, self.bounds().size.height))
        NSGraphicsContext.currentContext().saveGraphicsState()
        NSBezierPath.clipRect_(emptyRect)
        tiles = math.ceil(emptyWidth / float(self.backgroundCenterWidth))
        for i in range(0, int(tiles)):
            x = self.backgroundLeftWidth + (i * self.backgroundCenterWidth)
            self.backgroundCenter.compositeToPoint_operation_( (x, 0), NSCompositeSourceOver )
        NSGraphicsContext.currentContext().restoreGraphicsState()

    def drawTimeIndicator(self):
        timeStr = '00:00:00'
        if self.renderer is not None:
            seconds = self.renderer.getCurrentTime()
            timeStr = time.strftime("%H:%M:%S", time.gmtime(seconds))
        NSString.stringWithString_(timeStr).drawAtPoint_withAttributes_( (8,5), self.timeAttrs )

    def drawProgressGroove(self):
        rect = self.getGrooveRect()
        self.grooveFillColor.set()
        NSBezierPath.fillRect_(rect)
        self.grooveContourColor.set()
        NSBezierPath.strokeRect_(rect)

    def drawProgressCursor(self):
        if self.renderer == None:
            return

        progress = 0.0

        currentTime = self.renderer.getCurrentTime()
        if currentTime > 0.0:
            progress = self.renderer.getDuration() / currentTime

        offset = 0
        if progress > 0.0:
            offset = (self.getGrooveRect().size.width - 9) / progress

        x = math.floor(59 + offset) + 0.5
        self.cursor.compositeToPoint_operation_((x,7), NSCompositeSourceOver)

    def getGrooveRect(self):
        origin = NSPoint(60, 8)
        size = NSSize(self.bounds().size.width - 60 - 8, 8)
        return NSOffsetRect(NSRect(origin, size), -0.5, -0.5)


###############################################################################
#### An empty display                                                      ####
###############################################################################

class NullDisplay (app.Display):
    "Represents an empty right-hand area."

    def __init__(self):
        pool = NSAutoreleasePool.alloc().init()
        # NEEDS: take (and leak) a covering reference -- cargo cult programming
        self.view = WebView.alloc().init().retain()
        self.view.setCustomUserAgent_("DTV/pre-release (http://participatoryculture.org/)")
        app.Display.__init__(self)
        del pool

    def getView(self):
        return self.view


###############################################################################
#### Right-hand pane HTML display                                          ####
###############################################################################

class HTMLDisplay (app.Display):
    "HTML browser that can be shown in a MainFrame's right-hand pane."

#    sharedWebView = None

    # We don't need to override onSelected, onDeselected

    def __init__(self, html, existingView=None, frameHint=None, areaHint=None):
        """'html' is the initial contents of the display, as a string. If
        frameHint is provided, it is used to guess the initial size the HTML
        display will be rendered at, which might reduce flicker when the
        display is installed."""
        pool = NSAutoreleasePool.alloc().init()
        self.readyToDisplayHook = None
        self.readyToDisplay = False

#         if existingView == "sharedView":
#             if not HTMLDisplay.sharedWebView:
#                 HTMLDisplay.sharedWebView = WebView.alloc().init()
#                 HTMLDisplay.sharedWebView.setCustomUserAgent_("DTV/pre-release (http://participatoryculture.org/)")
#                 print "Creating sharedWebView: %s" % HTMLDisplay.sharedWebView
#             existingView = HTMLDisplay.sharedWebView

        self.web = ManagedWebView.alloc().init(html, None, self.nowReadyToDisplay, lambda x:self.onURLLoad(x), frameHint and areaHint and frameHint.getDisplaySizeHint(areaHint) or None)

        app.Display.__init__(self)
        del pool

    def getEventCookie(self):
        return ''
    def getDTVPlatformName(self):
        return 'webkit'

    def getView(self):
        return self.web.getView()

    def execJS(self, js):
        """Execute the given Javascript code (provided as a string) in the
        context of this HTML document."""
        try:
            self.web.execJS(js)
        except AttributeError:
            print "Couldn't exec javascript! Web view not initialized"
        #print "DISP: %s with %s" % (self.view, js)

    # DOM hooks used by the dynamic template code -- do they need a 
    # try..except wrapper like the above?
    def addItemAtEnd(self, xml, id):
        return self.web.addItemAtEnd(xml, id)
    def addItemBefore(self, xml, id):
        return self.web.addItemBefore(xml, id)
    def removeItem(self, id):
        return self.web.removeItem(id)
    def changeItem(self, id, xml):
        return self.web.changeItem(id, xml)
    def hideItem(self, id):
        return self.web.hideItem(id)
    def showItem(self, id):
        return self.web.showItem(id)

    def onURLLoad(self, url):
        """Called when this HTML browser attempts to load a URL (either
        through user action or Javascript.) The URL is provided as a
        string. Return true to allow the URL to load, or false to cancel
        the load (for example, because it was a magic URL that marks
        an item to be downloaded.) Implementation in HTMLDisplay always
        returns true; override in a subclass to implement special
        behavior."""
        # For overriding
        pass

    def callWhenReadyToDisplay(self, hook):
        # NEEDS: lock?
        if self.readyToDisplay:
            hook()
        else:
            assert self.readyToDisplayHook == None
            self.readyToDisplayHook = hook

    # Called (via callback established in constructor)
    def nowReadyToDisplay(self):
        self.readyToDisplay = True
        if self.readyToDisplayHook:
            hook = self.readyToDisplayHook
            self.readyToDisplayHook = None
            hook()

    def unlink(self):
        webView = self.web.getView()
        if webView is not nil:
            webView.setHostWindow_(self.currentFrame.obj.window()) # not very pretty
    
    def cancel(self):
        print "DTV: Canceling load of WebView %s" % self.web.getView()
        self.web.getView().stopLoading_(nil)
        self.readyToDisplay = False
        self.readyToDisplayHook = None
                        

###############################################################################
#### An enhanced WebView Wrapper                                           ####
###############################################################################

class ManagedWebView (NSObject):

    def init(self, initialHTML, existingView=nil, onInitialLoadFinished=None, onLoadURL=None, sizeHint=None):
        self.onInitialLoadFinished = onInitialLoadFinished
        self.onLoadURL = onLoadURL
        self.initialLoadFinished = False
        self.view = existingView
        if not self.view:
            self.view = WebView.alloc().init()
            print "***** Creating new WebView %s" % self.view
            if sizeHint:
                # We have an estimate of the size that will be assigned to
                # the view when it is actually inserted in the MainFrame.
                # Use this to size the view we just created so the HTML
                # is hopefully rendered to the correct dimensions, instead
                # of having to be corrected after being displayed.
                self.view.setFrame_(sizeHint)
            self.view.setCustomUserAgent_("DTV/pre-release (http://participatoryculture.org/)")
        else:
            print "***** Using existing WebView %s" % self.view
            if sizeHint:
                self.view.setFrame_(sizeHint)
        self.execQueue = []
        self.view.setPolicyDelegate_(self)
        self.view.setResourceLoadDelegate_(self)
        self.view.setFrameLoadDelegate_(self)
        self.view.setUIDelegate_(self)

        html = NSString.stringWithString_(unicode(initialHTML))
        data = html.dataUsingEncoding_(NSUTF8StringEncoding)
        self.view.mainFrame().loadData_MIMEType_textEncodingName_baseURL_(data, 'text/html', 'utf-8', nil)
        return self

    def isKeyExcludedFromWebScript_(self,key):
        return YES

    def isSelectorExcludedFromWebScript_(self,sel):
        if (str(sel) == 'eventURL'):
            return NO
        else:
            return YES

    def eventURL(self,url):
        self.onLoadURL(str(url))

    ##
    # Create CTRL-click menu on the fly
    def webView_contextMenuItemsForElement_defaultMenuItems_(self,webView,contextMenu,defaultMenuItems):
        menuItems = []
        if self.initialLoadFinished:
            exists = webView.windowScriptObject().evaluateWebScript_("typeof(getContextClickMenu)") == "function"
            if exists:
                x = webView.windowScriptObject().callWebScriptMethod_withArguments_("getContextClickMenu",[contextMenu['WebElementDOMNode']])
                if len(x) > 0:
                    # getContextClickMenu returns a string with one menu
                    # item on each line in the format
                    # "URL|description" Blank lines are separators
                    for menuEntry in x.split("\n"):
                        menuEntry = menuEntry.strip()
                        if len(menuEntry) == 0:
                            menuItems.append(NSMenuItem.separatorItem())
                        else:
                            (url, name) = menuEntry.split('|',1)
                            menuItem = NSMenuItem.alloc()
                            menuItem.initWithTitle_action_keyEquivalent_(name,self.processContextClick_,"")
                            menuItem.setEnabled_(YES)
                            menuItem.setRepresentedObject_(url)
                            menuItem.setTarget_(self)
                            menuItems.append(menuItem)
        return menuItems

    ##
    # Process a click on an item in a context menu
    def processContextClick_(self,item):
        self.execJS("document.location.href = \""+item.representedObject()+"\";")

    # Return the actual WebView that we're managing
    def getView(self):
        return self.view

    # Call func() once the document has finished loading. If the
    # document has already finished loading, call it right away. But
    # in either case, the call is executed on the main thread, by
    # queueing an event, since WebViews are not documented to be
    # thread-safe, and we have seen crashes.
    def execAfterLoad(self, func):
        if not self.initialLoadFinished:
            self.execQueue.append(func)
        else:
            AppHelper.callAfter(func)

    # Generate callbacks when the initial HTML (passed in the constructor)
    # has been loaded
    def webView_didFinishLoadForFrame_(self, webview, frame):
        if (not self.initialLoadFinished) and (frame is self.view.mainFrame()):
            # Execute any function calls we queued because the page load
            # hadn't completed
            # NEEDS: there should be a lock here, preventing execAfterLoad
            # from dropping something in the queue just after we have finished
            # processing it
            for func in self.execQueue:
                AppHelper.callAfter(func)
            self.execQueue = []
            self.initialLoadFinished = True

            if self.onInitialLoadFinished:
                self.onInitialLoadFinished()

            scriptObj = self.view.windowScriptObject()
            scriptObj.setValue_forKey_(self,'frontend')

    # Decorator to make using execAfterLoad easier
    def deferUntilAfterLoad(func):
        def runFunc(*args, **kwargs):
            func(*args, **kwargs)
        def schedFunc(self, *args, **kwargs):
            rf = lambda: runFunc(self, *args, **kwargs)
            self.execAfterLoad(rf)
        return schedFunc

    # Execute given Javascript string in context of the HTML document
    @deferUntilAfterLoad
    def execJS(self, js):
        self.view.stringByEvaluatingJavaScriptFromString_(js)

    # Intercept navigation actions and give program a chance to respond
    def webView_decidePolicyForNavigationAction_request_frame_decisionListener_(self, webview, action, request, frame, listener):
        method = request.HTTPMethod()
        url = request.URL()
        body = request.HTTPBody()
        type = action['WebActionNavigationTypeKey']
        #print "policy %d for url %s" % (type, url)
        # setting document.location.href in Javascript (our preferred
        # method of triggering an action) comes out as an
        # WebNavigationTypeOther.
        if type == WebNavigationTypeLinkClicked or type == WebNavigationTypeFormSubmitted or type == WebNavigationTypeOther:
            # Make sure we have a real, bona fide Python string, not an
            # NSString. Unfortunately, == can tell the difference.
            if (not self.onLoadURL) or self.onLoadURL('%s' % url):
                listener.use()
            else:
                listener.ignore()
        else:
            listener.use()

    # Redirect resource: links to files in resource bundle
    def webView_resource_willSendRequest_redirectResponse_fromDataSource_(self, webview, resourceCookie, request, redirectResponse, dataSource):
        url = "%s" % request.URL() # Make sure it's a Python string
        match = re.compile("resource:(.*)$").match(url)
        if match:
            path = resource.path(match.group(1))
            urlObject = NSURL.fileURLWithPath_(path)
            return NSURLRequest.requestWithURL_(urlObject)
        return request

    ## DOM mutators called, ultimately, by dynamic template system ##

    def findElt(self, id):
        doc = self.view.mainFrame().DOMDocument()
        elt = doc.getElementById_(id)
        return elt

#     def printHTML(self):
#         print
#         print "--- Document HTML ---"
#         print self.view.mainFrame().DOMDocument().body().outerHTML()
#         print "--- End Document HTML ---"

    def createElt(self, xml):
        parent = self.view.mainFrame().DOMDocument().createElement_("div")
        parent.setInnerHTML_(xml)
        elt = parent.firstChild()
        if parent.childNodes().length() != 1:
            raise NotImplementedError, "in createElt, expected exactly one node"
        return elt
        
    @deferUntilAfterLoad
    def addItemAtEnd(self, xml, id):
        elt = self.findElt(id)
        if not elt:
            print "warning: addItemAtEnd: missing element %s" % id
        else:
            elt.insertBefore__(self.createElt(xml), None)
            #print "add item %s at end of %s" % (elt.getAttribute_("id"), id)
            #print xml[0:79]

    @deferUntilAfterLoad
    def addItemBefore(self, xml, id):
        elt = self.findElt(id)
        if not elt:
            print "warning: addItemBefore: missing element %s" % id
        else:
            newelt = self.createElt(xml)
            elt.parentNode().insertBefore__(newelt, elt)
            #print "add item %s before %s" % (newelt.getAttribute_("id"), id)
            #print xml[0:79]

    @deferUntilAfterLoad
    def removeItem(self, id):
        elt = self.findElt(id)
        if not elt:
            print "warning: removeItem: missing element %s" % id
        else:
            elt.parentNode().removeChild_(elt)
            #print "remove item %s" % id

    @deferUntilAfterLoad
    def changeItem(self, id, xml):
        elt = self.findElt(id)
        if not elt:
            print "warning: changeItem: missing element %s" % id
        else:
            elt.setOuterHTML_(xml)
            #print "change item %s (new id %s)" % (id, elt.getAttribute_("id"))
            #print xml[0:79]
            #if id != elt.getAttribute_("id"):
            #    raise Exception
            #elt = self.findElt(id)
            #if not elt:
            #    print "ERROR ELEMENT LOST %s" % id

    @deferUntilAfterLoad
    def hideItem(self, id):
        elt = self.findElt(id)
        if not elt:
            print "warning: hideItem: missing element %s" % id
        else:
            elt.setAttribute__("style", "display:none")
            #print "hide item %s (new style '%s')" % (id, elt.getAttribute_("style"))

    @deferUntilAfterLoad
    def showItem(self, id):
        elt = self.findElt(id)
        if not elt:
            print "warning: showItem: missing element %s" % id
        else:
            elt.setAttribute__("style", "")
            #print "show item %s (new style '%s')" % (id, elt.getAttribute_("style"))


###############################################################################
#### The Playback Controller                                               ####
###############################################################################

class PlaybackController (app.PlaybackControllerBase):
    
    def playItemExternally(self, itemID):
        item = app.PlaybackControllerBase.playItemExternally(self, itemID)
        moviePath = item.getPath()
        ok = NSWorkspace.sharedWorkspace().openFile_withApplication_andDeactivate_(moviePath, nil, YES)
        if not ok:
            print "DTV: movie %s could not be externally opened" % moviePath
 

###############################################################################
#### Right-hand pane video display                                         ####
###############################################################################

class VideoDisplay (app.VideoDisplayBase):
    "Video player shown in a MainFrame's right-hand pane."

    def __init__(self):
        app.VideoDisplayBase.__init__(self)
        self.controller = VideoDisplayController.getInstance()
        self.controller.videoDisplay = self

    def canPlayItem(self, item):
        return self.controller.canPlayItem(item)

    def selectItem(self, item):
        self.controller.selectPlaylistItem(item)
        app.VideoDisplayBase.selectItem(self, item)

    def resetMovie(self):
        self.controller.resetMovie()

    def play(self):
        self.controller.play()
        app.VideoDisplayBase.play(self)

    def pause(self):
        self.controller.pause()
        app.VideoDisplayBase.pause(self)

    def stop(self):
        self.controller.stop()
        app.VideoDisplayBase.stop(self)
    
    def goFullScreen(self):
        self.controller.goFullScreen()
        app.VideoDisplayBase.goFullScreen(self)

    def exitFullScreen(self):
        self.controller.exitFullScreen()
        app.VideoDisplayBase.exitFullScreen(self)

    def getCurrentTime(self):
        return self.controller.getCurrentTime()

    def setVolume(self, level):
        self.controller.setVolume(level)
        app.VideoDisplayBase.setVolume(self, level)

    def getVolume(self):
        return self.controller.getVolume()

    def muteVolume(self):
        self.controller.volumeSlider.setEnabled_(NO)
        app.VideoDisplayBase.muteVolume(self)

    def restoreVolume(self):
        self.controller.volumeSlider.setEnabled_(YES)
        app.VideoDisplayBase.restoreVolume(self)

    def onSelected(self, frame):
        self.controller.onSelected()
        app.VideoDisplayBase.onSelected(self, frame)

    def onDeselected(self, frame):
        app.VideoDisplayBase.onDeselected(self, frame)
        self.controller.onDeselected()

    def getView(self):
        return self.controller.rootView


###############################################################################
#### The video display controller object, instantiated from the nib file   ####
###############################################################################

class VideoDisplayController (NibClassBuilder.AutoBaseClass):

    _instance = nil

    @classmethod
    def getInstance(self):
        assert VideoDisplayController._instance is not nil
        return VideoDisplayController._instance

    def awakeFromNib(self):
        VideoDisplayController._instance = self
        self.forwardButton.sendActionOn_(NSLeftMouseDownMask)
        self.backwardButton.sendActionOn_(NSLeftMouseDownMask)
        nc.addObserver_selector_name_object_(
            self, 
            'handleWatchableDisplayNotification:', 
            'displayIsWatchable', 
            nil)
        nc.addObserver_selector_name_object_(
            self, 
            'handleNonWatchableDisplayNotification:', 
            'displayIsNotWatchable', 
            nil)
        self.initRenderers()
        self.systemActivityUpdaterTimer = nil
        self.reset()

    def initRenderers(self):
        self.renderers = list()
        self.renderers.append(QuicktimeRenderer(self))
        self.renderer = None

    def getRendererForItem(self, item):
        renderer = None
        for r in self.renderers:
            if r.canPlayItem(item):
                renderer = r
                break
        return renderer

    def canPlayItem(self, item):
        return self.getRendererForItem(item) is not None

    def onSelected(self):
        self.enableSecondaryControls(YES)
        self.preventSystemSleep(True)
        self.videoAreaView.prepare()

    def onDeselected(self):
        self.enableSecondaryControls(False)
        self.preventSystemSleep(False)
        self.videoAreaView.teardown()
        self.progressDisplayer.teardown()
        self.reset()

    def selectPlaylistItem(self, item):
        renderer = self.getRendererForItem(item)
        renderer.selectPlaylistItem(item, self.volumeSlider.floatValue())
        self.videoAreaView.setup(renderer, item)
        self.progressDisplayer.setup(renderer)
        self.renderer = renderer

    def reset(self):
        if self.renderer is not None:
            self.renderer.reset()
        self.renderer = None
        self.currentWatchableDisplay = None
        self.fastSeekTimer = nil

    def resetMovie(self):
        self.renderer.gotoBeginning()

    def preventSystemSleep(self, prevent):
        if prevent and self.systemActivityUpdaterTimer is nil:
            print "DTV: Launching system activity updater timer"
            self.systemActivityUpdaterTimer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(30, self, 'updateSystemActivity:', nil, YES)
        elif self.systemActivityUpdaterTimer is not nil:
            print "DTV: Stopping system activity updater timer"
            self.systemActivityUpdaterTimer.invalidate()
            self.systemActivityUpdaterTimer = nil

    def updateSystemActivity_(self, timer):
        UpdateSystemActivity(OverallAct)

    def enablePrimaryControls(self, enabled):
        self.playPauseButton.setEnabled_(enabled)
        self.fullscreenButton.setEnabled_(enabled)
        self.muteButton.setEnabled_(enabled)
        self.volumeSlider.setEnabled_(enabled and self.muteButton.state() is NSOnState)

    def enableSecondaryControls(self, enabled):
        self.backwardButton.setEnabled_(enabled)
        self.stopButton.setEnabled_(enabled)
        self.forwardButton.setEnabled_(enabled)
        self.fullscreenButton.setEnabled_(enabled)

    def updatePlayPauseButton(self, prefix):
        self.playPauseButton.setImage_(NSImage.imageNamed_('%s.png' % prefix))
        self.playPauseButton.setAlternateImage_(NSImage.imageNamed_('%s_blue.png' % prefix))

    def playPause_(self, sender):
        app.Controller.instance.playbackController.playPause()

    def play(self):
        nc.postNotificationName_object_('videoWillPlay', nil)
        self.enablePrimaryControls(YES)
        self.enableSecondaryControls(YES)
        self.updatePlayPauseButton('pause')
        self.renderer.play()

    def pause(self):
        nc.postNotificationName_object_('videoWillPause', nil)
        self.updatePlayPauseButton('play')
        self.renderer.pause()

    def stop_(self, sender):
        app.Controller.instance.playbackController.stop()
        
    def stop(self):
        nc.postNotificationName_object_('videoWillStop', nil)
        self.updatePlayPauseButton('play')
        self.renderer.stop()

    def playFullScreen_(self, sender):
        app.Controller.instance.playbackController.playPause()
        self.videoDisplay.goFullScreen()

    def goFullScreen(self):
        self.videoAreaView.enterFullScreen()

    def exitFullScreen_(self, sender):
        self.exitFullScreen()

    def exitFullScreen(self):
        self.videoAreaView.exitFullScreen()

    def forward_(self, sender):
        self.performSeek(sender, 1)
        
    def skipForward_(self, sender):
        app.Controller.instance.playbackController.skip(1)

    def fastForward_(self, sender):
        self.performSeek(sender, 1, 0.0)
        
    def backward_(self, sender):
        self.performSeek(sender, -1)

    def skipBackward_(self, sender):
        app.Controller.instance.playbackController.skip(-1)

    def fastBackward_(self, sender):
        self.performSeek(sender, -1, 0.0)

    def performSeek(self, sender, direction, seekDelay=0.5):
        if sender.state() == NSOnState:
            sender.sendActionOn_(NSLeftMouseUpMask)
            info = {'seekDirection': direction}
            self.fastSeekTimer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(seekDelay, self, 'fastSeek:', info, NO)
            NSRunLoop.currentRunLoop().addTimer_forMode_(self.fastSeekTimer, NSEventTrackingRunLoopMode)
        else:
            sender.sendActionOn_(NSLeftMouseDownMask)
            if self.fastSeekTimer is nil:
                self.renderer.setRate(1.0)
            else:
                self.fastSeekTimer.invalidate()
                self.fastSeekTimer = nil
                app.Controller.instance.playbackController.skip(direction)

    def getCurrentTime(self):
        return self.renderer.getCurrentTime()

    def fastSeek_(self, timer):
        assert self.renderer.getRate() == 1.0
        info = timer.userInfo()
        direction = info['seekDirection']
        rate = 2 * direction
        self.renderer.setRate(rate)
        self.fastSeekTimer = nil

    def getVolume(self):
        return self.volumeSlider.floatValue()

    def setVolume_(self, sender):
        self.videoDisplay.setVolume(sender.floatValue())

    def setVolume(self, level):
        if self.muteButton.state() == NSOffState:
            level = 0.0
        if self.renderer is not None:
            self.renderer.setVolume(level)
        if self.muteButton.state() == NSOnState:
            self.volumeSlider.setFloatValue_(level)

    def muteUnmuteVolume_(self, sender):
        if sender.state() is NSOffState:
            self.videoDisplay.muteVolume()
        else:
            self.videoDisplay.restoreVolume()

    def handleWatchableDisplayNotification_(self, notification):
        self.enablePrimaryControls(YES)
        info = notification.userInfo()
        view = info['view']
        display = notification.object()
        app.Controller.instance.playbackController.configure(view, None, display)

    def handleNonWatchableDisplayNotification_(self, notification):
        self.enablePrimaryControls(NO)
        
    def handleMovieNotification_(self, notification):
        if notification.name() == QTMovieDidEndNotification and not self.progressDisplayer.dragging:
            app.Controller.instance.playbackController.onMovieFinished()


###############################################################################
#### The "dummy" video area. The actual video display will happen in a     ####
#### child VideoWindow window. This allows to have a single movie view for ####
#### both windowed and fullscreen playback                                 ####
###############################################################################

class VideoAreaView (NibClassBuilder.AutoBaseClass):
    
    def awakeFromNib(self):
        self.hostWindow = nil
    
    def prepare(self):
        self.hostWindow = self.window()
        assert self.hostWindow is not nil
        self.adjustVideoWindowFrame()
        self.hostWindow.addChildWindow_ordered_(self.videoWindow, NSWindowAbove)
    
    def setup(self, renderer, item):
        self.videoWindow.setup(renderer, item)
        self.videoWindow.orderFront_(nil)
    
    def teardown(self):
        if self.videoWindow.isFullScreen:
            self.videoWindow.exitFullScreen()
        self.hostWindow.removeChildWindow_(self.videoWindow)
        self.videoWindow.orderOut_(nil)
        self.videoWindow.teardown()
    
    def adjustVideoWindowFrame(self):
        if self.window() is nil:
            return
        frame = self.frame()
        frame.origin = self.convertPoint_toView_(NSZeroPoint, nil)
        frame.origin = self.window().convertBaseToScreen_(frame.origin)
        self.videoWindow.setFrame_display_(frame, YES)
        
    def setFrame_(self, frame):
        super(VideoAreaView, self).setFrame_(frame)
        self.adjustVideoWindowFrame()
    
    def enterFullScreen(self):
        self.adjustVideoWindowFrame()
        self.videoWindow.enterFullScreen()

    def exitFullScreen(self):
        if self.videoWindow.isFullScreen:
            self.videoWindow.exitFullScreen()
    

###############################################################################
#### The video window, used to display the movies in both windowed and     ####
#### fullscreen modes.                                                     ####
###############################################################################

class VideoWindow (NibClassBuilder.AutoBaseClass):
    
    def initWithContentRect_styleMask_backing_defer_(self, rect, style, backing, defer):
        self = super(VideoWindow, self).initWithContentRect_styleMask_backing_defer_(
            rect,
            NSBorderlessWindowMask,
            backing,
            defer )
        self.setAcceptsMouseMovedEvents_(YES)
        self.isFullScreen = NO
        return self

    def setup(self, renderer, item):
        if self.contentView() != renderer.view:
            self.setContentView_(renderer.view)
        self.palette.setup(item, renderer)
    
    def teardown(self):
        self.setContentView_(nil)

    def canBecomeMainWindow(self):
        return self.isFullScreen
    
    def canBecomeKeyWindow(self):
        return self.isFullScreen

    def enterFullScreen(self):
        SetSystemUIMode(kUIModeAllHidden, 0)
        self.isFullScreen = YES
        self.parent = self.parentWindow()
        self.frameInParent = self.frame()
        self.setFrame_display_animate_(NSScreen.mainScreen().frame(), YES, YES)
        self.parent.removeChildWindow_(self)
        self.parent.orderOut_(nil)
        self.makeKeyAndOrderFront_(nil)
        FullScreenAlertPanelController.displayIfNeeded()

    def exitFullScreen(self):
        self.isFullScreen = NO
        self.palette.remove()
        self.parent.addChildWindow_ordered_(self, NSWindowAbove)
        self.parent.makeKeyAndOrderFront_(nil)
        self.setFrame_display_animate_(self.frameInParent, YES, YES)
        SetSystemUIMode(kUIModeNormal, 0)
        
    def sendEvent_(self, event):
        if self.isFullScreen:
            if event.type() == NSLeftMouseDown:
                self.exitFullScreen()
            elif event.type() == NSKeyDown and event.characters().characterAtIndex_(0) == 0x1B:
                self.exitFullScreen()
            elif event.type() == NSMouseMoved:
                if not self.palette.isVisible():
                    self.palette.reveal(self)
                else:
                    self.palette.resetAutoConceal()


###############################################################################
#### Quicktime video renderer                                              ####
###############################################################################

class QuicktimeRenderer:

    def __init__(self, delegate):
        self.view = QTMovieView.alloc().initWithFrame_(((0,0),(100,100)))
        self.view.setFillColor_(NSColor.blackColor())
        self.view.setControllerVisible_(NO)
        self.view.setPreservesAspectRatio_(YES)
        self.delegate = delegate

    def registerMovieObserver(self, movie):
        self.unregisterMovieObserver()
        nc.addObserver_selector_name_object_(self.delegate, 'handleMovieNotification:', QTMovieDidEndNotification, movie)

    def unregisterMovieObserver(self):
        nc.removeObserver_name_object_(self.delegate, QTMovieDidEndNotification, nil)

    def reset(self):
        self.view.setMovie_(nil)
        self.unregisterMovieObserver()

    def canPlayItem(self, item):
        moviePath = item.getPath().lower()
        return not moviePath.endswith('wmv') and not moviePath.endswith('avi')        

    def selectPlaylistItem(self, item, volume):
        pathname = item.getPath()
        (qtmovie, error) = QTMovie.alloc().initWithFile_error_(pathname)
        if qtmovie is not nil:
            qtmovie.setVolume_(volume)
            self.view.setMovie_(qtmovie)
            self.registerMovieObserver(qtmovie)

    def play(self):
        self.view.play_(self)
        self.view.setNeedsDisplay_(YES)

    def pause(self):
        self.view.pause_(nil)

    def stop(self):
        self.view.pause_(nil)

    def gotoBeginning(self):
        if self.view.movie() is not nil:
            self.view.movie().gotoBeginning()

    def getDuration(self):
        if self.view.movie() is nil:
            return 0
        qttime = self.view.movie().duration()
        return qttime.timeValue / float(qttime.timeScale)

    def getCurrentTime(self):
        if self.view.movie() is nil:
            return 0
        qttime = self.view.movie().currentTime()
        return qttime.timeValue / float(qttime.timeScale)

    def setCurrentTime(self, time):
        if self.view.movie() is not nil:
            qttime = self.view.movie().currentTime()
            qttime.timeValue = time * float(qttime.timeScale)
            self.view.movie().setCurrentTime_(qttime)

    def getRate(self):
        if self.view.movie() is nil:
            return 0.0
        return self.view.movie().rate()

    def setRate(self, rate):
        if self.view.movie() is not nil:
            self.view.movie().setRate_(rate)
        
    def setVolume(self, level):
        if self.view.movie() is not nil:
            self.view.movie().setVolume_(level)
    
        
###############################################################################
#### Playlist item ?                                                       ####
###############################################################################

class PlaylistItem:
    "The record that makes up VideoDisplay playlists."

    def getTitle(self):
        """Return the title of this item as a string, for visual presentation
        to the user."""
        raise NotImplementedError

    def getPath(self):
        """Return the full path in the local filesystem to the video file
        to play."""
        raise NotImplementedError

    def getLength(self):
        """Return the length of this item in seconds as a real number. This
        is used only cosmetically, for telling the user the total length
        of the current playlist and so on."""
        raise NotImplementedError

    def onViewed(self):
        """Called by the frontend when a clip is at least partially watched
        by the user. To handle this event, for example by marking the
        item viewed in the database, override this method in a subclass."""
        raise NotImplementedError


###############################################################################
#### The fullscreen controls palette                                       ####
###############################################################################

class FullScreenPalette (NibClassBuilder.AutoBaseClass):
    
    HOLD_TIME = 10
    
    def initWithContentRect_styleMask_backing_defer_(self, rect, style, backing, defer):
        self = super(FullScreenPalette, self).initWithContentRect_styleMask_backing_defer_(
            rect,
            NSBorderlessWindowMask,
            backing,
            defer )
        self.setBackgroundColor_(NSColor.clearColor())
        self.setAlphaValue_(1.0)
        self.setOpaque_(NO)
        self.autoConcealTimer = nil
        self.updateTimer = nil
        self.holdStartTime = 0.0
        self.renderer = None
        return self

    def awakeFromNib(self):
        self.seekForwardButton.sendActionOn_(NSLeftMouseDownMask)
        self.seekBackwardButton.sendActionOn_(NSLeftMouseDownMask)
        self.progressSlider.setImages('fs-progress-background', 'fs-progress-slider')
        self.volumeSlider.setImages('fs-volume-background', 'fs-volume-slider')

    def canBecomeKeyWindow(self):
        return NO

    def canBecomeMainWindow(self):
        return NO

    def setup(self, item, renderer):
        self.titleLabel.setStringValue_(item.getTitle())
        self.feedLabel.setStringValue_(item.getFeed().getTitle())
        self.donationLabel.setStringValue_(u'')
        self.renderer = renderer

    def reveal(self, parent):
        screenSize = NSScreen.mainScreen().frame().size
        height = self.frame().size.height
        frame = ((0, -height), (screenSize.width, height))
        self.setFrame_display_(frame, NO)        
        parent.addChildWindow_ordered_(self, NSWindowAbove)
        self.orderFront_(nil)
        frame = ((0, 0), (screenSize.width, height))
        self.setFrame_display_animate_(frame, YES, YES)
        self.holdStartTime = time.time()
        self.autoConcealTimer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, 'concealAfterDelay:', nil, YES)
        self.updateTimer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, 'update:', nil, YES)
    
    def conceal(self):
        if self.autoConcealTimer is not nil:
            self.autoConcealTimer.invalidate()
            self.autoConcealTimer = nil
        if self.updateTimer is not nil:
            self.updateTimer.invalidate()
            self.updateTimer = nil
        frame = self.frame()
        frame.origin.y = -frame.size.height
        self.setFrame_display_animate_(frame, YES, YES)
        self.remove()
    
    def concealAfterDelay_(self, timer):
        if time.time() - self.holdStartTime > self.HOLD_TIME:
            self.conceal()
    
    def resetAutoConceal(self):
        self.holdStartTime = time.time()
        
    def expireNow_(self, sender):
        pass
        
    def tellAFriend_(self, sender):
        pass
        
    def update_(self, timer):
        self.updateTimeIndicator()
        self.updateProgressIndicator()
    
    def updateTimeIndicator(self):
        timeStr = '00:00:00'
        if self.renderer is not None:
            seconds = self.renderer.getCurrentTime()
            timeStr = time.strftime("%H:%M:%S", time.gmtime(seconds))
        self.timeIndicator.setStringValue_(timeStr)

    def updateProgressIndicator(self):
        if self.renderer is not None:
            progress = 0.0
            currentTime = self.renderer.getCurrentTime()
            if currentTime > 0.0:
                progress = currentTime / self.renderer.getDuration()
            self.progressSlider.value = progress
            self.progressSlider.setNeedsDisplay_(YES)
            
    def remove(self):
        if self.parentWindow() is not nil:
            self.parentWindow().removeChildWindow_(self)
        self.orderOut_(nil)


class FullScreenPaletteView (NibClassBuilder.AutoBaseClass):

    def awakeFromNib(self):
        self.background = NSImage.imageNamed_('fs-background')
        self.backgroundRect = NSRect((0,0), self.background.size())
        self.topLine = NSImage.imageNamed_('fs-topline')
        self.topLineRect = NSRect((0,0), self.topLine.size())

    def drawRect_(self, rect):
        width = self.bounds().size.width
        bgRect = ((0,0), (width, self.backgroundRect.size.height))
        self.background.drawInRect_fromRect_operation_fraction_(bgRect, self.backgroundRect, NSCompositeSourceOver, 1.0)
        tlRect1 = ((0,self.backgroundRect.size.height), (width-135, self.topLineRect.size.height))
        self.topLine.drawInRect_fromRect_operation_fraction_(tlRect1, self.topLineRect, NSCompositeSourceOver, 1.0)
        tlRect2 = ((width-25,self.backgroundRect.size.height), (25, self.topLineRect.size.height))
        self.topLine.drawInRect_fromRect_operation_fraction_(tlRect2, self.topLineRect, NSCompositeSourceOver, 1.0)


class FullScreenControlsView (NibClassBuilder.AutoBaseClass):
    
    def awakeFromNib(self):
        self.background = NSImage.imageNamed_('fs-controls-background')
        self.backgroundRect = NSRect((0,0), self.background.size())

    def drawRect_(self, rect):
        self.background.compositeToPoint_operation_((0, 0), NSCompositeSourceOver)
        
    def hitTest_(self, point):
        # Our buttons have transparent parts, but we still want mouse clicks
        # to be detected if they happen there, so we override hit testing and
        # simply test for button frames.
        for subview in self.subviews():
            if NSPointInRect(self.convertPoint_fromView_(point, nil), subview.frame()):
                return subview
        return self


class FullScreenSlider (NibClassBuilder.AutoBaseClass):

    def awakeFromNib(self):
        self.value = 0.0

    def setImages(self, track, knob):
        self.track = NSImage.imageNamed_(track)
        self.knob = NSImage.imageNamed_(knob)
    
    def drawRect_(self, rect):
        kx = (self.frame().size.width * self.value) - (self.knob.size().width * self.value)
        self.track.compositeToPoint_operation_((0, 2), NSCompositeSourceOver)
        self.knob.compositeToPoint_operation_((kx, 0), NSCompositeSourceOver)
