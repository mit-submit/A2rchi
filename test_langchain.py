from a2rchi.utils.data_manager import DataManager
from a2rchi.chains.chain import Chain

# place to fix:
# 1.update chroma in langchain by 'pip install -U langchain-chroma'
#   and use 'from langchain_chroma import Chroma' to replace 'from langchain.vectorstores import Chroma'
# 2.

class DiscourseAIWrapper:
    def __init__(self):
        self.chain = Chain()
        self.data_manager = DataManager()
        self.data_manager.update_vectorstore()

    def __call__(self, post):

        formatted_history = []

        formatted_history = [['User', post]]

        # form the formatted history using the post

        self.data_manager.update_vectorstore()

        answer = self.chain(formatted_history)
        return answer
    

# question = 'Hello, I am trying to make a box over a TF1 in the Legend. I have something like this: where fstat is a TF1 legendcell = new TLegend(0.67,0.53,0.98,0.7+0.015*(years.size()));fstat->SetLineColor(429);fstat->SetFillColor(5);fstat->SetFillStyle(1001); egendcell->AddEntry(fstat, "#bf{" + legend + "}" ,"fl"); legendcell->Draw(); The problem is that this also creates a filling in the TGraph. Is there any way to create a box on the TLegend without changing the drawing on the canvas? PS: I also tried to do fstat->SetFillStyle(0); after the drawing of the legend but this also removes the box from the TLegend My root version is 6.28/00 I really appreciate any help you can provide.'
question = 'Hello, I am trying to make a box over a TF1 in the Legend. I have something like this: where fstat is a TF1 legendcell = new TLegend(0.67,0.53,0.98,0.7+0.015*(years.size()));fstat->SetLineColor(429);fstat->SetFillColor(5);fstat->SetFillStyle(1001); egendcell->AddEntry(fstat, "#bf{" + legend + "}" ,"fl"); legendcell->Draw(); The problem is that this also creates a filling in the TGraph. Is there any way to create a box on the TLegend without changing the drawing on the canvas? PS: I also tried to do fstat->SetFillStyle(0); after the drawing of the legend but this also removes the box from the TLegend My root version is 6.28/00 I really appreciate any help you can provide.'


archi = DiscourseAIWrapper()
answer = archi(question)
print("\n\n\n")
print(answer)
print("\n\n\n")
